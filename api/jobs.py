# -*- coding: utf-8 -*-
"""JobQueue — 사이드카의 단일 워커 큐(설계 [[vinc.spec.dataplane §4]], R4).

임베디드 스토어는 인프로세스 단일 Database 다. 여러 HTTP 핸들러 스레드가 동시에
쓰면 커넥션·트랜잭션이 경합한다. 그래서 **모든 뮤테이션을 단일 워커 스레드로
직렬화**한다 — 이 워커가 "쓰기 지점"이다(R4). 조회는 큐를 우회해 별도 read
Connection 으로 직접 처리하므로(§store.embedded_backend `reader_view`), 긴 쓰기 잡
진행 중에도 응답성을 유지한다(§11 P2 게이트 ②).

두 실행 형태:
  - ``run_sync(kind, fn)`` — enqueue 후 완료까지 대기(짧은 뮤테이션: pin/tag/touch).
    CLI 와 동일한 결과 dict 를 그대로 반환해 HTTP↔CLI 응답 동등성(①)을 지킨다.
  - ``submit(kind, fn)`` — enqueue 후 즉시 ``job_id`` 반환(긴 잡: ingest·backup).
    ``GET /jobs/{id}`` 폴링으로 상태를 조회한다.

stdlib 전용(``queue``·``threading``). 잡 id 는 결정론적 단조 카운터(테스트 친화 —
벽시계·난수 미사용, 설계의 순수·재현 원칙과 정합).
"""
from __future__ import annotations

import queue
import sys
import threading
from contextvars import copy_context
from dataclasses import dataclass, field
from typing import Callable

from store.write_waits import WRITE_WAITS, current_kind

# 잡 상태 리터럴(설계 §11 P2 — POST /jobs 202 → GET /jobs/{id} 폴링).
QUEUED = "queued"
RUNNING = "running"
DONE = "done"
ERROR = "error"
# This internal diagnostic uses the writer connection for serialized reads only.
# It must not mark the database dirty or trigger a durable copy on completion.
READ_ONLY_KINDS = frozenset({"topic-conversion.preflight"})


@dataclass
class Job:
    id: str
    kind: str
    status: str = QUEUED
    result: dict | None = None
    error: str | None = None
    write_may_have_applied: bool = False
    _done: threading.Event = field(default_factory=threading.Event, repr=False)

    def to_dict(self) -> dict:
        """직렬화 표면(HTTP 바디) — 내부 Event 는 제외."""
        result = {"job_id": self.id, "kind": self.kind, "status": self.status,
                  "result": self.result, "error": self.error}
        if self.status == ERROR and self.write_may_have_applied:
            result.update(retryable=False, write_may_have_applied=True)
        return result


class JobError(RuntimeError):
    """run_sync 대상 잡이 error 로 끝났을 때(HTTP 500 매핑용)."""

    def __init__(self, message, *, job_id=None, write_may_have_applied=False):
        super().__init__(message)
        self.job_id = job_id
        self.write_may_have_applied = write_may_have_applied


# run_sync 기본 대기 상한. **무한 대기 금지**이되 **거짓 실패도 금지**라 값이 크다 —
# 이보다 오래 걸릴 수 있는 일(백업·인박스 적재·동기화)은 애초에 `submit`(202)을 쓴다.
# 두 거짓말의 대칭: [[record:a-bounded-wait-must-not-manufacture-false-failure]].
DEFAULT_SYNC_TIMEOUT = 600.0


class JobQueue:
    """단일 워커 스레드가 잡을 FIFO 로 직렬 실행(R4 쓰기 지점).

    잡 함수 ``fn: () -> dict`` 는 워커 스레드에서 실행된다 — 그 안의 스토어 접근은
    자동으로 직렬화되므로 임베디드 단일 커넥션 쓰기 경합이 없다.

    ``on_idle`` 은 잡 하나가 끝날 때마다(``wrote=True``), 그리고 ``tick_s`` 를 주면
    큐가 비어 있어도 그 주기마다(``wrote=False``) 불린다(쓰기 스레드). 영속 스냅샷처럼
    **드라이버를 닫았다 여는** 작업은 여기서만 안전하다 — 다른 스레드에서 하면
    재오픈 중인 커넥션을 누군가 쓴다. 예외는 삼키되 조용히 넘기지는 않는다.

    ``on_before_done(job, result)`` 은 성공 응답 전에 반드시 끝나야 하는 작업의
    자리다. 예외가 나면 이 잡은 ERROR 이며 결과를 공개하지 않는다. 이미 실행된
    ``fn`` 의 로컬 뮤테이션이 되돌아갔다는 뜻은 아니므로 재시도 정책은 호출자가
    별도로 정해야 한다.

    ``tick_s`` 가 없으면(기본) 워커는 잡이 올 때까지만 깬다. 그러면 잡 완료가 유일한
    틱이라 **버스트의 마지막 쓰기는 다음 쓰기가 올 때까지** 아무 후처리도 못 밟는다 —
    허브의 영속 스냅샷이 거기서 잤고 ack 를 준 문서를 잃었다(2026-09-06). 그래서
    사이드카는 이 값을 준다. 틱은 잡의 순서도 지연도 바꾸지 않는다: 대기에 상한만
    둘 뿐이고 ``Queue.get`` 은 항목이 들어오면 즉시 깬다."""

    def __init__(self, *, on_idle=None, on_before_done=None, on_before_run=None,
                 tick_s: float | None = None, waits=None) -> None:
        self._on_idle = on_idle
        # 누가 누구 뒤에서 얼마나 기다렸나(`store.write_waits`). 수만 센다.
        self._waits = waits if waits is not None else WRITE_WAITS
        # A required durability hook runs on the sole writer before a successful
        # response or DONE can be observed. Unlike on_idle, its error is fatal to
        # this job; the caller must treat a post-mutation failure as indeterminate.
        self._on_before_done = on_before_done
        self._on_before_run = on_before_run
        self._tick_s = tick_s
        self._q: "queue.Queue" = queue.Queue()
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._counter = 0
        self._stopping = threading.Event()
        self._worker: threading.Thread | None = None
        self._revivals = 0
        self._ensure_worker()

    # ── 워커 수명 ─────────────────────────────────────────────────────────
    def _ensure_worker(self) -> None:
        """워커가 살아 있게 보장한다 — 없거나 죽었으면 새로 띄운다.

        **단일 워커 불변식**(R4 쓰기 지점)은 락 + `is_alive()` 검사로 지킨다: 둘이
        동시에 돌면 임베디드 커넥션 쓰기 경합이라는 이 큐의 존재 이유가 무너진다.

        되살리기가 필요한 이유는 아래 `_run` 의 방어가 뚫릴 수 있어서가 아니라,
        **뚫렸을 때 프로세스 전체가 벽돌이 되지 않게** 하기 위해서다(2026-07-31
        실측: 잡 하나가 SystemExit 을 던져 워커가 죽자 그 뒤 모든 쓰기가 영구
        무응답이 됐고, 재시작 말고는 복구 수단이 없었다)."""
        with self._lock:
            if self._stopping.is_set():
                return
            if self._worker is not None and self._worker.is_alive():
                return
            if self._worker is not None:
                self._revivals += 1
            self._worker = threading.Thread(target=self._run, name="vinc-job-worker",
                                            daemon=True)
            self._worker.start()

    def worker_alive(self) -> bool:
        w = self._worker
        return bool(w and w.is_alive())

    # ── id ────────────────────────────────────────────────────────────────
    def _next_id(self) -> str:
        with self._lock:
            self._counter += 1
            return f"job-{self._counter}"

    # ── 제출 ──────────────────────────────────────────────────────────────
    def submit(self, kind: str, fn: Callable[[], dict], *, _sync: bool = False) -> Job:
        """잡을 큐에 넣고 즉시 Job(status=queued) 반환(비동기, 202 매핑)."""
        job = Job(self._next_id(), kind)
        with self._lock:
            self._jobs[job.id] = job
        context = copy_context()
        def guarded():
            current_kind.set(kind)   # 이 잡 안에서 잡힌 DDL 배타를 이 종류로 센다(복사한 컨텍스트라 새지 않는다)
            if self._on_before_run is not None:
                self._on_before_run(job)
            return fn()
        # 큐에 넣기 **전에** 센다 — 워커가 먼저 집어 가면 시작이 대기보다 앞선다.
        self._waits.enqueued(job.id, kind, sync=_sync)
        self._q.put((job, lambda: context.run(guarded)))
        self._ensure_worker()   # 앞선 잡이 워커를 무너뜨렸어도 이 잡은 돈다
        return job

    def run_sync(self, kind: str, fn: Callable[[], dict],
                 *, timeout: float | None = DEFAULT_SYNC_TIMEOUT) -> dict:
        """잡을 넣고 완료까지 대기해 결과 dict 반환(동기, 짧은 뮤테이션). 잡이
        error 로 끝나면 :class:`JobError` 를 올린다(HTTP 500). 워커는 하나뿐이라
        이 대기가 곧 쓰기 직렬화다.

        ``timeout=None`` 이면 무한 대기다 — **기본값이 아니다**(DEFAULT_SYNC_TIMEOUT).
        무한 대기는 한 번 막히면 화면이 영원히 "처리 중"에 굳는 실패 양식이었다."""
        job = self.submit(kind, fn, _sync=True)
        if not job._done.wait(timeout=timeout):
            self._waits.timed_out(kind)
            # 잡은 여전히 큐/실행 중일 수 있다 — 그 사실을 숨기지 않는다.
            raise JobError(
                f"job {job.id}({kind}) 가 {timeout}s 안에 끝나지 않았습니다. "
                f"작업은 계속될 수 있습니다(status={job.status}).",
                job_id=job.id, write_may_have_applied=True)
        if job.status == ERROR:
            raise JobError(job.error or f"job {job.id} failed", job_id=job.id,
                           write_may_have_applied=job.write_may_have_applied)
        return job.result if job.result is not None else {}

    # ── 조회 ──────────────────────────────────────────────────────────────
    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    # ── 워커 루프 ─────────────────────────────────────────────────────────
    def _run(self) -> None:
        while True:
            try:
                # 대기에 상한만 둔다 — 잡이 들어오면 그 즉시 깨므로 순서·지연은 그대로다.
                item = self._q.get(timeout=self._tick_s) if self._tick_s else self._q.get()
            except queue.Empty:
                # 종료가 시작됐으면 틱은 여기서 끝이다. `shutdown()` 이 `_q.join()`
                # **앞에서** 이 플래그를 세우고, 그 뒤 호출부가 메인 스레드에서
                # 마지막 스냅샷과 store.close() 를 한다. 그때 워커가 유휴 틱으로
                # 스냅샷(드라이버 close→reopen) 안에 있으면 5초 join 이 만료되고
                # 같은 경로를 두 스레드가 동시에 밟는다.
                if self._stopping.is_set():
                    continue
                # 큐가 빈 채로 한 주기가 지났다. 쓰기가 없었으므로 `wrote=False` 를
                # 실어 보낸다 — 호출부는 이것으로 "표시할 것"과 "때가 됐나 볼 것"을
                # 가른다. 여기서 `task_done()` 을 부르면 드레인 회계가 깨진다.
                self._idle_tick(wrote=False)
                continue
            if item is None:            # sentinel — shutdown
                self._q.task_done()
                break
            job, fn = item
            job.status = RUNNING
            self._waits.started(job.id)
            try:
                result = fn()
                normalized = result if isinstance(result, dict) else {"result": result}
                if self._on_before_done is not None and job.kind not in READ_ONLY_KINDS:
                    try:
                        self._on_before_done(job, normalized)
                    except BaseException:
                        # 뮤테이션 뒤 게시 실패는 롤백이 아니다. 재시도 전에 결과를 확인한다.
                        job.write_may_have_applied = True
                        raise
                job.result = normalized
                job.status = DONE
            # ⚠ **BaseException 이다** — `Exception` 으로 좁히지 말 것. `SystemExit`·
            # `KeyboardInterrupt` 가 빠져나가면 **워커 스레드가 죽고** 그 뒤 모든 쓰기가
            # 영구 무응답이 된다(읽기는 멀쩡해 오진하기 쉽다):
            # [[record:job-isolation-has-to-catch-baseexception]].
            except BaseException as exc:   # noqa: BLE001 — 잡 격리(어떤 잡도 워커를 못 죽인다)
                job.error = f"{type(exc).__name__}: {exc}"
                job.status = ERROR
            finally:
                self._waits.finished(job.id, ok=job.status == DONE)
                job._done.set()
                self._q.task_done()
            self._idle_tick(wrote=job.kind not in READ_ONLY_KINDS)

    def _idle_tick(self, *, wrote: bool) -> None:
        """쓰기 스레드에서 할 일(영속 스냅샷)을 부른다.

        ``wrote`` 는 이 틱이 잡 완료 뒤인지(True) 빈 큐 타이머인지(False)를 가른다.
        이 큐가 곧 쓰기 경로라 호출부에는 그것 말고 "쓰기가 있었는가"를 알 재료가
        없다 — 그래서 판단을 여기서 넘긴다."""
        try:
            if self._on_idle is not None:
                # 후처리도 워커를 붙잡는다 — 그동안 기다린 쓰기에 이 시간을 단다.
                with self._waits.blocking():
                    self._on_idle(wrote=wrote)
            self._waits.maybe_log()
        except Exception as exc:                      # noqa: BLE001
            print(f"[jobs] on_idle 실패(계속 진행): {type(exc).__name__}: {exc}",
                  file=sys.stderr, flush=True)

    # ── 체크포인트는 여기 없다 ─────────────────────────────────────────────
    # 잡 개수로 세는 체크포인트를 두지 말 것. 엔진이 WAL **바이트** 기준으로 스스로
    # 하고(임계는 `StoreConfig.checkpoint_threshold_mb`), 수동 CHECKPOINT 는 읽기까지
    # 포함한 활성 트랜잭션에 걸려 실패 로그만 남는다:
    # [[record:job-count-checkpointing-was-removed]].

    # ── 종료 ──────────────────────────────────────────────────────────────
    def shutdown(self, *, drain: bool = True) -> None:
        """워커를 정리한다(설계 §11 P2 E6 — clean shutdown). ``drain`` 이면 남은
        잡을 모두 처리한 뒤 종료한다(backup·ingest 유실 방지)."""
        if self._stopping.is_set():
            return
        self._stopping.set()
        if drain:
            self._q.join()              # 대기·실행 중 잡 완료까지 블록
        self._q.put(None)               # 워커 루프 종료 sentinel
        w = self._worker
        if w is not None:
            w.join(timeout=5.0)
        try:
            self._waits.maybe_log(force=True)   # 재시작으로 비기 전의 마지막 집계
        except Exception:                       # noqa: BLE001 — 종료를 막지 않는다
            pass
