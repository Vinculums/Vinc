# -*- coding: utf-8 -*-
"""JobQueue — 사이드카의 단일 워커 큐(설계 [[weg.c.memory-standalone]] §11 P2·R4).

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
import threading
from dataclasses import dataclass, field
from typing import Callable

# 잡 상태 리터럴(설계 §11 P2 — POST /jobs 202 → GET /jobs/{id} 폴링).
QUEUED = "queued"
RUNNING = "running"
DONE = "done"
ERROR = "error"


@dataclass
class Job:
    id: str
    kind: str
    status: str = QUEUED
    result: dict | None = None
    error: str | None = None
    _done: threading.Event = field(default_factory=threading.Event, repr=False)

    def to_dict(self) -> dict:
        """직렬화 표면(HTTP 바디) — 내부 Event 는 제외."""
        return {"job_id": self.id, "kind": self.kind, "status": self.status,
                "result": self.result, "error": self.error}


class JobError(RuntimeError):
    """run_sync 대상 잡이 error 로 끝났을 때(HTTP 500 매핑용)."""


# run_sync 기본 대기 상한. **무한 대기를 두지 않는다**(2026-07-31 실측 사고): 워커가
# 어떤 이유로든 진행하지 못하면 그때부터 모든 쓰기 요청이 영원히 매달려, 화면은
# "처리 중"에 굳고 사용자는 무엇이 됐는지 알 길이 없다.
#
# 값이 큰 이유: 여기서 **거짓 실패**를 만들면 안 된다. 큰 팩 적재처럼 정당하게 오래
# 걸리는 잡이 상한에 걸리면, 데이터는 들어갔는데 화면은 실패라고 말한다 — 무한 대기와
# 다른 종류의 거짓말이다. 10분은 어떤 대화형 기대치보다도 길되 유한하다. 이보다 더
# 걸릴 수 있는 일(백업·인박스 적재·동기화)은 애초에 `submit`(비동기 202)을 쓴다.
DEFAULT_SYNC_TIMEOUT = 600.0


class JobQueue:
    """단일 워커 스레드가 잡을 FIFO 로 직렬 실행(R4 쓰기 지점).

    잡 함수 ``fn: () -> dict`` 는 워커 스레드에서 실행된다 — 그 안의 스토어 접근은
    자동으로 직렬화되므로 임베디드 단일 커넥션 쓰기 경합이 없다."""

    def __init__(self):
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
    def submit(self, kind: str, fn: Callable[[], dict]) -> Job:
        """잡을 큐에 넣고 즉시 Job(status=queued) 반환(비동기, 202 매핑)."""
        job = Job(self._next_id(), kind)
        with self._lock:
            self._jobs[job.id] = job
        self._q.put((job, fn))
        self._ensure_worker()   # 앞선 잡이 워커를 무너뜨렸어도 이 잡은 돈다
        return job

    def run_sync(self, kind: str, fn: Callable[[], dict],
                 *, timeout: float | None = DEFAULT_SYNC_TIMEOUT) -> dict:
        """잡을 넣고 완료까지 대기해 결과 dict 반환(동기, 짧은 뮤테이션). 잡이
        error 로 끝나면 :class:`JobError` 를 올린다(HTTP 500). 워커는 하나뿐이라
        이 대기가 곧 쓰기 직렬화다.

        ``timeout=None`` 이면 무한 대기다 — **기본값이 아니다**(DEFAULT_SYNC_TIMEOUT).
        무한 대기는 한 번 막히면 화면이 영원히 "처리 중"에 굳는 실패 양식이었다."""
        job = self.submit(kind, fn)
        if not job._done.wait(timeout=timeout):
            # 잡은 여전히 큐/실행 중일 수 있다 — 그 사실을 숨기지 않는다.
            raise JobError(
                f"job {job.id}({kind}) 가 {timeout}s 안에 끝나지 않았습니다. "
                f"작업은 계속될 수 있습니다(status={job.status}).")
        if job.status == ERROR:
            raise JobError(job.error or f"job {job.id} failed")
        return job.result if job.result is not None else {}

    # ── 조회 ──────────────────────────────────────────────────────────────
    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    # ── 워커 루프 ─────────────────────────────────────────────────────────
    def _run(self) -> None:
        while True:
            item = self._q.get()
            if item is None:            # sentinel — shutdown
                self._q.task_done()
                break
            job, fn = item
            job.status = RUNNING
            try:
                result = fn()
                job.result = result if isinstance(result, dict) else {"result": result}
                job.status = DONE
            # ⚠ **BaseException 이다** — `Exception` 이 아니다(2026-07-31 실측 사고).
            # 잡 격리는 이 큐의 존재 이유이고, `except Exception` 은 그 약속을 못
            # 지킨다: `SystemExit`·`KeyboardInterrupt` 는 BaseException 이라 그대로
            # 빠져나가 **워커 스레드를 죽인다**. 실제로 임베딩 스택이 없는 기기에서
            # `/doc/reembed` 잡이 라이브러리의 sys.exit() 를 밟아 워커가 죽었고, 그
            # 뒤 모든 쓰기가 큐에 쌓인 채 영구 무응답이 됐다(읽기는 별도 커넥션이라
            # 멀쩡해 오진하기 쉬웠다). 워커 스레드에서 SystemExit 은 프로세스를
            # 끝내지도 못하므로, 잡 하나를 실패로 접는 것이 언제나 옳다.
            except BaseException as exc:   # noqa: BLE001 — 잡 격리(어떤 잡도 워커를 못 죽인다)
                job.error = f"{type(exc).__name__}: {exc}"
                job.status = ERROR
            finally:
                job._done.set()
                self._q.task_done()

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
