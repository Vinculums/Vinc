# -*- coding: utf-8 -*-
"""사이드카 HTTP 서버 — CLI 데이터 플레인 위 얇은 루프백 레이어(설계 [[vinc.spec.architecture §2]]).

데스크톱 셸(Tauri)이 인프로세스 데이터 플레인과 통신하는 통로다. 계약 3가지:

  1. **CLI 1:1** — 엔드포인트가 `api/cli.py` 서브커맨드와 1:1 대응하고, 각 `q/pref/act`
     함수가 반환하는 dict 를 그대로 바디로 낸다(HTTP↔CLI 응답 동등성 — §11 P2 ①).
  2. **쓰기 직렬화(R4)** — 모든 뮤테이션은 단일 워커 큐(`api/jobs.py`)로 흐른다.
     짧은 뮤테이션(pin/tag/touch)은 동기(run_sync→200), 긴 작업(ingest/backup)은
     비동기(submit→202+job_id, `GET /jobs/{id}` 폴링). 조회는 큐를 우회해 별도 read
     Connection(`store.reader_view`)으로 처리 → 잡 실행 중에도 응답성 유지(§11 P2 ②).
  3. **로컬 인증(F4)** — 루프백(127.0.0.1) 바인딩 + 기동 시 발급한 세션 토큰
     (`Authorization: Bearer`)으로만 응답. `/health` 만 무인증.

제어(수명주기)=부모 프로세스 heartbeat / 데이터=HTTP 로 분리한다(§3-1 E6). 부모
(셸) 사망 시 clean shutdown(워커 드레인→스토어 close→서버 stop).

stdlib 전용(`http.server`·`threading`·`signal`) — DEC D5(threaded stdlib + 자체 잡
큐, 부족 시 FastAPI). 코어 의존성 0.
"""
from __future__ import annotations

from api.procutil import pid_alive

import base64
import datetime as _dt
import json
import os
import secrets
import signal
import sys
import inspect
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

#: 쿼리 임베딩 대기 상한(초) — **정반대 두 고장을 다른 값으로** 다룬다.
#:
#:   짧은 쪽(`_QVEC_TIMEOUT`)은 인코더가 **바쁜** 경우다. 조회 요청 안에서 도는
#:   호출이라, 워커가 배경 임베딩으로 붐비면 기다리느니 FTS 로 답하는 편이 낫다
#:   (§11 P2 게이트).
#:   긴 쪽(`_QVEC_COLD_TIMEOUT`)은 인코더가 **없는** 경우다(레플리카 0에서 깨는 중).
#:   깨는 데는 오래 걸리지만 한 번 깨면 그 뒤 질의는 싸다 — 여기서 1.5초에 포기하면
#:   벡터 팔이 영영 안 켜진다.
#:
#: 둘을 가르는 신호는 **최근 성공 시각**이다(`VincApp._claim_cold_retry`). 긴 값을 무는
#: 질의는 한 질의당 한 번뿐이고, 인코더가 죽어 있으면 `_QVEC_COLD_COOLDOWN` 마다 하나이며
#: 나머지는 전부 `_QVEC_TIMEOUT` 에서 끊는다.
#:
#: ⚠ **이 값들은 총 상한이 아니라 소켓 연산당 상한이다.** `urlopen(timeout=T)` 은 연결에도
#: 매 read 에도 각각 T 를 주므로, 응답을 찔끔찔끔 흘리는 인코더에는 총 지연이 T 를 얼마든지
#: 넘는다(실측: T=1.5 에 10.2초 걸리고 **예외 없이 성공**). 그러니 "1.5+30"을 상류 예산의
#: 천장으로 잡지 마라. 총 지연을 실제로 끊는 것은 브리지 요청 상한 하나뿐이고
#: (`vinc_mcp/bridge.py` `request(timeout=60)`) 거기 걸리면 강등이 아니라 호출 자체가
#: 실패한다 — 콜드 값을 키울 때 먼저 볼 곳이 그 60초다.
#:
#: ⚠ 기다림을 늘리는 값이므로 그 기다림이 보여야 한다 — 안 보이면 "가끔 느린 검색"이
#: 새 무음 결함이 된다. 지금 남는 곳은 **사이드카 stderr 한 줄뿐**이다(`_query_embedding`).
#: `vector_note` 로는 못 낸다 — 그 필드의 계약이 "의미 팔이 안 돌았을 때만 나타난다"라서
#: (`vinc_mcp/tools.py`) 성공한 콜드 재시도에 붙이면 계약이 깨진다. 화면에 내려면 계약을
#: 먼저 고칠 것.
_QVEC_TIMEOUT = float(os.environ.get("VINC_EMBED_ENCODE_TIMEOUT", "1.5"))
_QVEC_COLD_TIMEOUT = float(os.environ.get("VINC_EMBED_ENCODE_COLD_TIMEOUT", "30"))
_QVEC_WARM_WINDOW = float(os.environ.get("VINC_EMBED_ENCODE_WARM_WINDOW", "300"))
_QVEC_COLD_COOLDOWN = float(os.environ.get("VINC_EMBED_ENCODE_COLD_COOLDOWN", "120"))


def _is_timeout(exc: BaseException) -> bool:
    """시간 초과인가. urlopen 은 두 모양으로 낸다 — 읽기는 `TimeoutError`
    (=`socket.timeout`), 연결은 그것을 `reason` 에 감싼 `URLError`. 연결 거부·401 은
    여기 해당하지 않는다(빨리 오는 답이라 다시 기다릴 이유가 없다)."""
    return isinstance(exc, TimeoutError) or isinstance(
        getattr(exc, "reason", None), TimeoutError)


#: 미러 재구축 한 배치의 크기. 인코더 왕복 수와 한 요청의 무게를 맞바꾼다.
_MIRROR_BATCH = int(os.environ.get("VINC_NODE_MIRROR_BATCH", "32"))
#: 그 한 배치의 상한. 질의 경로의 1.5초와 **다른 성질**이다 — 여기서는 기다리는 쪽이
#: 배포 스크립트라 인코더 콜드스타트(0→1 리비전)를 통째로 참는 것이 맞다.
_MIRROR_ENCODE_TIMEOUT = float(os.environ.get("VINC_NODE_MIRROR_ENCODE_TIMEOUT", "120"))
#: 교체(드롭·삭제·삽입·인덱스 빌드)의 상한. 인코딩이 빠져 있어 짧다.
_MIRROR_REPLACE_TIMEOUT = float(os.environ.get("VINC_NODE_MIRROR_REPLACE_TIMEOUT", "600"))


def _encode_post(url: str, texts: list[str], kind: str, timeout: float) -> list:
    """인코딩 창구에 한 번 묻는다(`packaging/embed_worker.py` `serve_encode`).

    ⚠ **토큰을 실어야 한다**(2026-08-07). 창구가 루프백일 때는 인증이 없었는데, 모델을
    `vinc-encode` 로 분리하면서 인증이 붙었다. 배치 워커만 고치고 질의 경로를 빠뜨려
    **질의 임베딩이 전부 401** 이었다 — 배경 적재는 정상이라 겉으로는 다 되는 것처럼
    보였다. 그래서 창구를 부르는 자리를 여기 한 벌로 모았다.

    실패는 **던진다** — 부르는 쪽이 시간 초과 여부를 보고 다시 갈지 정한다."""
    body = json.dumps({"texts": list(texts), "kind": kind}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    tok = os.environ.get("VINC_EMBED_ENCODE_TOKEN", "").strip()
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    req = urllib.request.Request(url.rstrip("/") + "/encode", data=body,
                                 headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return (json.load(resp) or {}).get("vectors") or []

from store.config import load_config
from store import config as store_config
from store.backend import open_store

from . import query as q
from . import preference as pref
from . import activate as act
from . import fragment as frag
from . import share
from . import spaceproxy
from . import changes
from . import localmode
from . import sync
from . import docstore
from . import node_mirror
from . import docsync
from . import graphmd
from . import textextract
from .auth import SessionAuth
from .backup import make_backup
from .jobs import JobQueue, JobError
from store.persistence import (SnapshotPolicy, claim_writer, resolve_snapshot_every,
                               resolve_snapshot_path, save_snapshot)


# ── ingest 디스패치(비동기 잡) ───────────────────────────────────────────────
# ingest 는 데이터 플레인 엔진(engines/*)을 워커 스레드에서 돌린다 — 유일 writer 로
# 직렬화(R4). 여기서는 대표 경로(recall 에피소드 적재)를 배선한다. 나머지 엔진 배선은
# 동형 증분(엔진은 이미 구현됨 — M2/M3). store 미도달 시 강등(무 크래시).

def _ingest_recall(store, body: dict) -> dict:
    """recall 에피소드 배치를 writer 세션으로 MERGE 적재(멱등). 잡 경유 = 직렬화."""
    episodes = body.get("episodes") or []
    if store is None:
        return {"engine": "recall", "executed": False, "ingested": 0,
                "note": "no reachable store"}
    from engines.recall_ingest import merge_episode
    n = 0
    with store.session() as s:
        for ep in episodes:
            merge_episode(s, ep)
            n += 1
    return {"engine": "recall", "executed": True, "ingested": n}


DEFAULT_INGEST = {"recall": _ingest_recall}


def store_unavailable() -> dict:
    """스토어 도달 불가의 **사유 봉투** — `/health` 와 조회 503 이 같은 것을 말하게.

    `EmbeddedBackend.open` 은 실패 사유를 `last_open_error` 에 담는다(락 충돌·손상·
    확장 경로). 그 값을 읽는 곳이 `/health` 하나뿐이면, 조회가 거절당한 클라이언트는
    거절만 받고 이유는 다른 엔드포인트에 가서 물어야 한다. 한 벌로 묶는다."""
    out = {"error": "no reachable store", "note": "no reachable store"}
    try:
        from store.embedded_backend import EmbeddedBackend
        if EmbeddedBackend.last_open_error:
            out["open_error"] = EmbeddedBackend.last_open_error
    except Exception:   # noqa: BLE001 — 진단이 응답을 죽이지 않는다
        pass
    return out


# ── 앱(라우트 핸들러의 상태 컨테이너) ─────────────────────────────────────────

#: 팀 그래프 owner 의 접두. 정본은 `vinc_mcp/http_server._TEAM_OWNER_RE` 이고
#: 여기 있는 것은 **사본**이다 — 사이드카는 MCP 층을 import 하지 않는다(다른 프로세스).
#: 둘이 어긋나면 팀 그래프가 personal 로 보고되므로 교차계약 테스트가 대조한다
#: (`record:fact-ap1-role-table-mirror` 와 같은 처방).
_TEAM_OWNER_PREFIX = "team:"


def _owner_scope(owner: str | None) -> str:
    """이 owner 가 어느 종류의 그래프인가 — `personal` · `team` · `unscoped`.

    `unscoped` 는 owner 없이 도는 로컬 단일 사용자 스토어다. 빈 문자열로 두면
    호출부가 "모른다"와 "개인이다"를 구분할 수 없어서 이름을 준다."""
    if not owner:
        return "unscoped"
    return "team" if owner.startswith(_TEAM_OWNER_PREFIX) else "personal"


def connect_origins() -> list[str]:
    """계정 연결 콜백을 보낼 수 있는 웹 오리진.

    도메인 이전(vincs.app→vincs.io): 두 오리진을 모두 허용한다. 기존 배포 데스크탑
    클라이언트는 vincs.app/connect-app 를, 신규는 vincs.io 를 연다 — 병행 필수.
    `VINC_CONNECT_ORIGINS`(콤마 구분)로 오버라이드 가능.

    ⚠ 두 곳이 이 목록을 써야 한다 — `connect_callback` 의 POST 게이트와 `do_OPTIONS`
    의 사설망 승인이다. 둘이 어긋나면 preflight 는 통과하는데 본 요청이 403 이 된다.
    """
    return [o.strip() for o in os.environ.get(
        "VINC_CONNECT_ORIGINS", "https://vincs.app,https://vincs.io").split(",") if o.strip()]


def _omit_absent_rows(res: dict) -> dict:
    """recall 응답에서 **값이 없는 키를 뺀다**. 지우는 것이 아니라 생략이다.

    `tenant`·`workspace`·`access_count`·`last_access` 는 MCP 로 쓸 수 없어서, MCP 로만
    먹인 스토어에서는 모든 행에 빈 값으로 실려 나간다. 화면에 빈칸이 줄줄이 나가면
    기능 결손처럼 읽히고, 행마다 토큰도 문다.

    ⚠ **필드를 없애는 것과 다르다.** 없애면 그 값들의 읽기 경로가 사라지고, 그것은
    [[decision:every-written-field-has-a-read-path]] 와 정면으로 부딪힌다 — 이 넷은
    배치 임포트와 preference 신호가 채울 수 있고, 그때는 저절로 다시 나와야 한다.
    생략은 토큰을 똑같이 줄이면서 계약을 안 바꾼다.

    ⚠ **여기가 전송 경계라서 여기 있다.** 질의(`api/query.py`)는 rank 무관 전량을
    투영하는데 그건 의도다: 이 신호는 런타임 축적분이라 원문 재수집으로 복원되지
    않고, `api/activate.py` 가 **같은 행으로 컨텍스트 팩을 만든다**(팩이 유일한 무손실
    이관 통로, 설계 §10·D4). 질의에서 빼면 팩이 그 신호를 잃는다.

    ⚠ **0 은 값이다.** `access_count: 0` 은 "없음"이 아니라 "0회"라서 남긴다. 뺄 것은
    `None` 뿐이다 — 부재와 0 을 뭉개면 이 함수가 판단을 하기 시작한다.
    """
    rows = res.get("rows")
    if isinstance(rows, list):
        res["rows"] = [{k: v for k, v in row.items() if v is not None}
                       if isinstance(row, dict) else row for row in rows]
    return res


class VincApp:
    """사이드카 상태: 설정 · 공유 스토어(단일 Database, writer) · 세션 인증 · 잡 큐 ·
    ingest 레지스트리 · 백업 소스 경로. 라우트 메서드는 (status, dict) 를 돌려준다."""

    def __init__(self, cfg=None, *, store=None, auth=None, jobs=None,
                 packs_dir=None, profile_path=None, ingest=None, themes=None,
                 provision_error=None):
        self.cfg = cfg or load_config()
        self.store = store                    # 공유 writer 백엔드(단일 Database) 또는 None
        # 기동 시 스키마 생성이 실패했다면 그 사유(없으면 None) — /health 로 노출해
        # "왜 조회가 다 깨지나"를 앱이 바로 알 수 있게 한다(open() 주석 참조).
        self.provision_error = provision_error
        self.auth = auth or SessionAuth()
        # 영속 스냅샷 정책(§ store/persistence.py). `VINC_STORE_SNAPSHOT` 미설정이면
        # 전부 no-op 이다 — 데스크탑·로컬 개발은 영향 없다.
        #
        # 잡 큐에 무는 이유: 스냅샷은 드라이버를 **닫았다 다시 여는** 구간이 있어
        # 쓰기 스레드에서만 안전하다. 쓰기는 이 큐 하나로 직렬화되므로 여기가
        # 유일하게 옳은 자리다.
        # 유휴 틱 = 스냅샷 주기의 1/10. 잡이 없어도 워커가 깨어 `due()` 를 보므로
        # 손실 창이 "다음 쓰기가 올 때까지"에서 `every` + 틱 + 복사 시간으로 줄어든다.
        #
        # ⚠ 스냅샷을 끄는 것은 **`VINC_STORE_SNAPSHOT` 미설정**이지 주기가 아니다.
        # `resolve_snapshot_every()` 는 그 env 와 무관하게 기본 300 을 주므로, 주기만
        # 보고 배선하면 스냅샷이 꺼진 데스크탑 사이드카에서도 워커가 30초마다 깨는
        # 영구 타이머가 된다(배터리). 꺼짐 판정은 `SnapshotPolicy` 가 생성자에서 이미
        # 들고 있다 — `last_state == "off"` 가 곧 경로 미설정이다.
        self._snapshots = SnapshotPolicy()
        tick_s = (self._snapshots.every / 10.0
                  if self._snapshots.last_state != "off" else 0.0)
        self.jobs = jobs or JobQueue(on_idle=self._maybe_snapshot,
                                     tick_s=tick_s or None)
        self.packs_dir = packs_dir
        self.profile_path = profile_path
        self.ingest = ingest if ingest is not None else dict(DEFAULT_INGEST)
        self._sync_running = False            # 동시 sync 거부 플래그(S2, 커서 경합 방지)
        # 열린 화면이 다시 읽을 때를 아는 값(VIN-129). 메모리에만 있다(`api.changes`).
        self.changes = changes.ChangeMarker()
        # 주제·타입 어휘 스토어(그래프 밖 메타 — <data_dir>.themes.json, DB 파일 형제).
        # D11-R2. 그래프(store)와 분리 — 어휘는 사용자 저작, 그래프는 인스턴스.
        from store.theme_store import ThemeStore
        self.themes = themes if themes is not None else ThemeStore(self.cfg.data_dir)
        self._conn_lock = threading.Lock()    # reader Connection 파생 가드
        # 미러 재구축은 통째로 갈아 끼우는 일이라 **두 벌이 겹치면 안 된다** —
        # 하나가 인덱스를 드롭한 사이 다른 하나가 만들면 반쪽 미러가 남는다.
        self._mirror_lock = threading.Lock()
        self._reader_takes_writer: bool | None = None  # 스토어가 writer= 를 받는가(1회 판정)
        self._mig_note = ""      # 임베딩 모델 이행 중 사유(빈 문자열 = 정상)
        self._mig_at = 0.0       # 그 판정 시각 — 30초 캐시(`_embed_migrating`)
        self._mig_pending = 0    # 이행 중 남은 문서 수(GM-13 배지 진행도, note 와 동시 갱신)
        # 쿼리 임베딩 창구 상태 — 콜드스타트와 '바쁨'을 가르는 재료(`_claim_cold_retry`).
        # ⚠ 아래 셋은 전부 `time.monotonic()` 눈금이다(벽시계 아님). NTP 역방향 스텝이나
        # VM 재개가 시계를 밀면 '웜'·쿨다운 판정이 오류도 로그도 없이 뒤집힌다.
        self._qvec_ok = float("-inf")  # 마지막 성공 시각(-inf = 아직 한 번도 성공 못 했다)
        self._qvec_cold_until = 0.0  # 이 시각 전에는 긴 상한을 쓰지 않는다(죽은 인코더 가드)
        self._qvec_cold_busy = False # 지금 긴 상한으로 기다리는 스레드가 있다
        self._qvec_lock = threading.Lock()
        # 계정 연결 메일박스(디바이스 연결 플로우) — state 논스 → {expires,[key,email]}.
        # 인메모리 전용(재기동 시 소멸 — 연결 진행 중이면 다시 시작). B1 유지: 로그인·
        # 키 발급은 전부 브라우저(vincs.app)에서 일어나고, 앱은 발급된 키를 받기만 한다.
        self._connect: dict[str, dict] = {}
        self._connect_lock = threading.Lock()

    @classmethod
    def open(cls, cfg=None, **kw) -> "VincApp":
        cfg = cfg or load_config()
        store = open_store(cfg)                        # 드라이버 미설치 시 store=None
        provision_error = None
        if store is not None:
            # 첫 실행 스키마 멱등 생성. serve 경로엔 별도 provision 단계가 없어(데스크톱
            # 앱은 `vinc serve` 만 띄운다) 미프로비저닝 스토어면 모든 적재가
            # 'Table Concept does not exist' 로 실패한다. store.provision 은 멱등이라
            # 매 기동 안전(이미 있으면 무시). 별도 store 를 여는 store.provision.provision()
            # 은 배타 락 충돌 때문에 쓰지 않고, 이미 열린 store 에 직접 적용한다.
            try:
                store.provision(dim=cfg.embedding_dim)
            except Exception as exc:
                # **기동을 죽이지 않는다**(2026-07 macOS 리포트 대응): 프로비저닝 실패가
                # 예외로 새어 나가면 serve 가 통째로 죽어 포트에 아무도 남지 않고, 앱은
                # 원인 불명의 "health TIMEOUT" 만 본다. 스토어 상태 문제(구버전 잔존
                # 스토어·락·손상)는 조회 응답의 error 로 표면화하는 편이 진단 가능하다.
                # 기동은 계속하되 사유를 stderr(셸이 수집)와 /health 에 남긴다.
                provision_error = f"{type(exc).__name__}: {exc}"
                print(f"vinc: provision failed — {provision_error}", file=sys.stderr)
        if store is not None and provision_error is None:
            _heal_vector_index(store, cfg.embedding_dim)
        app = cls(cfg, store=store, provision_error=provision_error, **kw)
        if store is not None and provision_error is None:
            app._schedule_mentions_rule()
        return app

    def _schedule_mentions_rule(self) -> None:
        """MENTIONS 링크 규칙 표식 대조(`engines.mentions_link.ensure_rule`)를 잡 큐에 건다.

        기동을 막지 않고(submit — 비동기) 쓰기는 단일 워커로 직렬화된다(R4). 표식이
        같으면 읽기 한 번이고, 다르면 전량 재링크다. 이 훅이 있는 이유: 재링크는
        `put_doc` 에서만 일어나므로 규칙만 바꿔 배포하면 **이미 적재된 문서의 링크는
        옛 규칙 그대로** 남는다(임베딩 모델 전환과 같은 형태의 조용한 드리프트).
        실패는 기동을 죽이지 않는다 — 링크는 증강이다."""
        store = self.store

        def _run() -> dict:
            from engines.mentions_link import ensure_rule
            res = ensure_rule(store)
            if res.get("changed"):
                print(f"vinc: MENTIONS 재링크 — 규칙 {res.get('previous') or '(없음)'}"
                      f" → {res.get('rule')} · {res.get('owners')}", file=sys.stderr)
            # owner 를 귀속 못 한 청크는 전량 재링크가 **건너뛴다.** 조용히 넘어가면
            # "전량이라고 적힌 경로가 아무것도 안 한" 상태가 표식만 남기고 굳는다 —
            # 2026-09-06 에 실제로 그렇게 굳어 있었다.
            if (res.get("ownerless_remaining") or 0) > 0:
                print(f"vinc: ⚠ owner 없는 청크 {res['ownerless_remaining']}개 — "
                      "재링크가 건너뛴다(문서 메타가 없는 고아)", file=sys.stderr)
            return res

        try:
            self.jobs.submit("mentions.rule", _run)
        except Exception as exc:      # noqa: BLE001 — 기동 경로, 죽이지 않는다
            print(f"vinc: mentions rule check skipped — {exc}", file=sys.stderr)

    # ── 영속 스냅샷 ──────────────────────────────────────────────────────
    def _maybe_snapshot(self, *, force: bool = False, wrote: bool = True) -> str | None:
        """때가 됐으면 작업본을 영속 볼륨으로 복사한다. **잡 워커 스레드 전용.**

        주기가 곧 최대 손실 창이다(기본 300초). 쓰기가 있었을 때만 뜬다 — 읽기만
        오가는 동안 수십 MB 를 계속 미는 것은 비용만이다.

        `wrote` 는 부른 쪽이 잡을 하나 끝냈는지(True), 빈 큐 타이머인지(False)다.
        **False 에서 dirty 를 세우면 안 된다** — 그러면 읽기만 오가는 밤에도 계속 뜬다.
        반대로 표시와 판정을 한 호출에 묶어 두면(예전 형태) 표시한 그 호출은 아직 때가
        아니고 다음 호출은 **다음 쓰기**뿐이라, 버스트의 마지막 쓰기가 거기서 잤다."""
        if not force:
            # 잡이 하나 끝났다 = 쓰기가 있었을 수 있다. 잡 큐가 곧 쓰기 경로이므로
            # 표시를 호출부에 흩뿌리지 않고 여기서 한 번에 든다. 빈 큐 틱은 그 자리가
            # 아니다 — 표시 없이 때만 본다.
            if wrote:
                self._snapshots.mark_dirty()
            if not self._snapshots.due():
                return None
        state = save_snapshot(self.store)
        self._snapshots.done(state)
        if state.startswith("error"):
            print(f"vinc: store snapshot — {state}", file=sys.stderr, flush=True)
        elif not force and state != "off":
            # **성공도 찍는다.** 여태 성공은 아무 흔적을 안 남겼고, 그래서 로그에서
            # "마지막으로 안전해진 시각"을 알 수 없었다 — 2026-08-19 에 배포가 무엇을
            # 삼켰는지 사후에 셀 수 없었던 이유가 이것이다([[vinc.ops.deployment]]).
            # force 경로는 close() 가 따로 찍으므로 여기서는 주기 스냅샷만.
            print(f"vinc: store snapshot — {state} (every {self._snapshots.every:.0f}s)",
                  file=sys.stderr, flush=True)
        return state

    # reader 뷰: 공유 Database 에서 별도 Connection(R4). 없으면 None(각 q 함수가
    # 자체 open→None→강등). 요청 단위로 생성·close.
    def _reader(self):
        if self.store is None:
            return None
        with self._conn_lock:
            # `reader_view` 는 **스토어 인터페이스**다 — 다른 백엔드·테스트 더블도
            # 구현한다. 새 키워드를 강제하면 그것들이 통째로 깨지므로(실측: 더블이
            # TypeError → 500), 지원하는 스토어에만 넘긴다.
            if self._reader_takes_writer is None:
                self._reader_takes_writer = _accepts_kw(self.store.reader_view, "writer")
            if self._reader_takes_writer:
                return self.store.reader_view(writer=self._at_write_point)
            return self.store.reader_view()

    def _at_write_point(self, label, fn, timeout):
        """읽기 요청 중 불가피한 쓰기를 **쓰기 지점으로 돌린다**(R4).

        해당하는 것은 둘: 검색 인덱스 지연 빌드(T3b)와 그래프 알고리즘의 project_graph.
        둘 다 DDL = 쓰기다. 읽기 스레드가 자기 리더 커넥션에서 이걸 실행하면 엔진의
        단일 쓰기 트랜잭션을 붙들어, 그동안 잡 워커의 평범한 저장조차 `Cannot start a
        new write transaction` 로 실패한다(2026-07-31 실측).

        타임아웃을 두는 이유: 이 호출은 **조회 요청 안**에 있다. 앞에 긴 적재 잡이
        있으면 조회가 그만큼 매달리는데, 그때는 강등하는 편이 낫다(호출부가 각각
        index_build_failed·algo 실패 note 로 표면화한다). 실측 소요는 1초 미만.

        ``timeout=0`` 은 **예약**이다: 잡만 걸고 결과를 기다리지 않는다. 검색이 벡터
        인덱스 재빌드를 거는 자리가 쓴다(`_ReaderView.schedule_search_indexes`) —
        그 답은 이번 응답에 필요하지 않고, 검색은 그동안 FTS 로 답한다."""
        if timeout is not None and timeout <= 0:
            return self.jobs.submit(label, fn).to_dict()
        return self.jobs.run_sync(label, fn, timeout=timeout)

    def _read(self, fn):
        """읽기 경로 공통: reader 뷰를 열어 fn(reader)->dict 실행 후 닫는다.

        **스토어가 안 열렸으면 여기서 503 이다.** 여태 이 자리는 `r=None` 을 그대로
        아래로 흘렸고, 그 아래 계약은 제각각이었다(2026-08-16 전수 실측, store 도달
        불가 상태의 GET 31개):

          - 다수(`/graph`·`/recall`·`/search` …)는 **200** + `executed:false`.
            HTTP 만 보는 클라이언트에게는 "성공했고 데이터가 없다"로 읽힌다.
          - `/doc`·`/docs`·`/conventions`·`/doc/highlights`·`/sync/domains`·
            `/sync/docs-export`·`/embed/pending` 7개는 `None.run` 을 불러
            **500 `AttributeError`** 로 터졌다(사유가 없는 500).
          - `/sync/pull-delta` 는 **`executed:true`** + "no domains selected —
            nothing leaves the device". 읽을 수 없는 스토어를 **빈 스토어로 보고**
            했다. 동기화에서 이건 가장 나쁜 거짓말이다.

        게다가 `q.*` 는 `store=None` 을 받으면 **자기가 스토어를 다시 연다**
        (`query.execute`: `owns = store is None` → `open_store(cfg)`). 즉 공유
        스토어가 락으로 못 열린 상태에서 조회마다 같은 경로를 다시 열려 시도했다.
        여기서 끊으면 그 두 번째 열기도 함께 사라진다.

        `/health` 는 이 판정을 쓰지 않는다 — 거긴 200 + `status:degraded` 가 계약이고
        (릴리스 스모크가 그 값을 본다), 프로브가 503 이면 오케스트레이터가 살아 있는
        사이드카를 죽인다. 진단은 답하고 데이터는 거절하는 것이 옳은 조합이다."""
        r = self._reader()
        if r is None:
            # 본문에 executed:false 를 함께 싣는다 — 기존 클라이언트(앱·CLI·MCP)가
            # 이미 그 필드로 강등을 판정하고, 브리지는 HTTPError 본문을 그대로
            # 통과시키므로 MCP 호스트도 같은 사유를 본다.
            return 503, {"executed": False, **store_unavailable()}
        try:
            return 200, fn(r)
        finally:
            r.close()

    def close(self):
        """clean shutdown(E6): 워커 드레인 → **마지막 스냅샷** → 공유 스토어 close.

        순서가 중요하다. 드레인 뒤에 해야 큐에 남은 쓰기까지 담기고, close 앞에
        해야 스토어가 살아 있다. 이 한 번이 "정상 종료면 아무것도 안 잃는다"를
        만든다 — 주기 스냅샷은 급사에 대비한 것이고 이건 예고된 종료용이다.
        (SIGKILL·OOM 은 여기 못 온다. 그때 잃는 양의 상한이 스냅샷 주기다.)"""
        self.jobs.shutdown(drain=True)
        if self.store is not None:
            state = self._maybe_snapshot(force=True)
            if state and state != "off":
                print(f"vinc: final snapshot — {state}", file=sys.stderr, flush=True)
            self.store.close()
            self.store = None

    # ── 읽기 라우트(CLI 1:1) ──────────────────────────────────────────────
    def read_recall(self, p) -> tuple[int, dict]:
        return self._read(lambda r: _omit_absent_rows(q.recall(
            p.get("query", ""), owner=p.get("owner"), domain=p.get("domain"),
            tenant=p.get("tenant"), workspace=p.get("workspace"),
            rank=p.get("rank", "recency"), tags=_csv(p.get("tags")),
            limit=_int(p.get("limit"), 10), store=r, debug=_flag(p.get("debug")),
            # 커서 페이징은 opt-in 이다 — `page`·`cursor` 없이 오는 호출(웹 op recall)은
            # 응답 모양이 그대로다. [[vinc.plan.mcp-read-tool-fixes-2026-09-24]] §3 S3.
            page=_flag(p.get("page")), cursor=p.get("cursor") or None)))

    def read_domains(self, p):
        return self._read(lambda r: q.domains(owner=p.get("owner"), store=r,
                                              debug=_flag(p.get("debug"))))

    def read_owner_stats(self, p):
        # 소유자 전체의 노드·문서 수(W1c). 허브의 계정 서명 경로 `/internal/usage` 만
        # 부른다 — 웹 프록시(`_PROXY_OPS`)에 올리지 않는다(`q.owner_stats` 주석).
        return self._read(lambda r: q.owner_stats(store=r))

    def read_learner(self, p):
        return self._read(lambda r: q.learner_model(owner=_req(p, "owner"), store=r))

    def read_graph(self, p):
        # seed 지정 시 seed+depth ego 서브그래프(View L2 = MCP vinc_graph 동일 경로).
        # layout = 프리셋(radial·circle·tree·organic·rank·dendro — 미지 값 radial 강등).
        # basis(D72) = 시간축. 미지 값은 조용히 recorded 로 강등하고 응답이 에코한다.
        return self._read(lambda r: q.graph(owner=_req(p, "owner"),
                                            domain=p.get("domain"), seed=p.get("seed"),
                                            # 주제 여럿(쉼표 결합). 스코프는 하위
                                            # 트리 포함이다 — query.normalize_domains.
                                            domains=_domains(p.get("domains")),
                                            depth=_int(p.get("depth"), 2),
                                            layout=p.get("layout") or "radial",
                                            # 원뿔 반각 배율(1.0 = 기본). 범위 클램프는
                                            # query 가 한다 — 상한의 근거가 기하라서
                                            # 계산하는 쪽에 두어야 한 곳에서만 산다.
                                            cone=_float(p.get("cone"), 1.0),
                                            basis=p.get("basis") or q.BASIS_RECORDED,
                                            # 노드 예산 — MCP vinc_graph 만 보낸다. 안
                                            # 보내면 None 이고 결과는 종전과 같다.
                                            # 근거: [[decision:vinc-graph-node-budget-lives-in-the-mcp-handler-2026-09-24]]
                                            max_nodes=_int(p.get("max_nodes"), 0) or None,
                                            store=r))

    def read_view(self, p):
        # View L0(D16) — 도메인 분포 + 결합 행렬.
        return self._read(lambda r: q.view_l0(owner=_req(p, "owner"), store=r))

    def read_ledger(self, p):
        # 원장(출처 × 주제 × 연결상태) 롤업 — 조회 전용. 쓰기 큐를 타지 않는다(R4).
        # seed 지정 시 ego(시드 주변) 롤업 — 우선순위 seed > domain > all(D43).
        return self._read(lambda r: q.ledger(owner=_req(p, "owner"),
                                             domain=p.get("domain"),
                                             seed=p.get("seed"),
                                             depth=_int(p.get("depth"), 2),
                                             by=p.get("by") or "domain", store=r))

    def read_promote(self, p):
        # Phase E1 — pin 후보 조회(읽기 전용). 확정은 기존 /pin 경유.
        return self._read(lambda r: q.promote_candidates(
            owner=_req(p, "owner"), min_access=_int(p.get("min_access"), 3), store=r))

    def read_suggest(self, p):
        # 고아 최소화(D32) — 태그·도메인 근사 잇기 후보(조회 전용, D25 전 단계).
        return self._read(lambda r: q.suggest_ties(
            owner=_req(p, "owner"), node=_req(p, "node"),
            limit=_int(p.get("limit"), 5), store=r))

    def read_coupling(self, p):
        # Compare 뷰(D22) — 두 도메인의 개념 대 개념 결합쌍.
        return self._read(lambda r: q.coupling(owner=_req(p, "owner"),
                                               a=_req(p, "a"), b=_req(p, "b"), store=r))

    def read_neighbors(self, p):
        # E-G5 단일 홉 관측(SoG) — 개념의 1-hop 관계망 요약(조회 전용).
        return self._read(lambda r: q.neighbors(
            _req(p, "node"), owner=_req(p, "owner"),
            limit=_int(p.get("limit"), 50), store=r))

    def read_path(self, p):
        # E-G7/E-G8 최단 경로 증거 체인 — 두 개념 사이 경로(조회 전용).
        # weight=confidence 면 가중 갈래(E-G8 다익스트라), 그 외는 홉 최단(기본).
        return self._read(lambda r: q.shortest_path(
            _req(p, "a"), _req(p, "b"), max_depth=_int(p.get("max_depth"), 6),
            owner=_req(p, "owner"), weight=p.get("weight") or q.WEIGHT_HOPS, store=r))

    def read_merge_candidates(self, p):
        # E-G4 병합 후보(조회 전용) — 확정은 /merge-nodes 경유(자동 병합 금지 T1).
        # F10: 구조적 비중복(한쪽이라도 문서 개념 — 문서끼리·문서↔주제)과 기각 기억은 기본 제외,
        # include_structural=1 로 되열 수 있다(억제 건수는 suppressed 로 노출).
        return self._read(lambda r: q.suggest_merge_candidates(
            owner=_req(p, "owner"), min_score=float(p.get("min_score") or 0.6),
            limit=_int(p.get("limit"), 20),
            include_structural=_flag(p.get("include_structural")), store=r))

    def read_merge_rejections(self, p):
        # F10-b 기각 목록(조회 전용) — 무엇을 되살릴 수 있는지 보여 준다.
        return self._read(lambda r: q.list_merge_rejections(owner=_req(p, "owner"), store=r))

    def read_diagnose(self, p):
        # E-G3 구조 진단(조회 전용) — WCC 컴포넌트/고아 + Louvain 커뮤니티. kind=
        # components(기본) | communities | community_context(단일 시드의 군집 +
        # 대표 발췌 — unified-kg-rag-comparison §4). algo 확장 부재 시 executed=False 강등.
        # claims(R5)는 algo 를 안 쓴다 — 확장이 없어도 답한다.
        kind = (p.get("kind") or "components").lower()
        owner = _req(p, "owner")
        if kind == "community_context":
            return self._read(lambda r: q.community_context(
                owner=owner, node=_req(p, "node"), store=r,
                member_limit=_int(p.get("member_limit"), 0) or None))
        if kind == "claims":
            # R5 — 유일하게 **구조가 아니라 props 내용**을 읽는 kind. 나머지 셋과
            # 이웃한 코드가 아니라서 component_diagnosis 를 넓히지 않고 따로 뒀다.
            # 근거: [[vinc.ops.graph-claim-decay]] §7.3.
            return self._read(lambda r: q.claim_diagnosis(
                owner=owner, store=r, limit=_int(p.get("member_limit"), 0) or None))
        if kind == "communities":
            return self._read(lambda r: q.communities(
                owner=owner, store=r, member_limit=_int(p.get("member_limit"), 0) or None))
        if kind != "components":
            # 모르는 kind 를 **말없이 components 로 답하지 않는다**. 여태 오타 하나가
            # 전혀 다른 진단을 성공으로 받아 갔고, 그것이 이 PR 이 다루는 고장이다
            # (성공이라고 답하면서 시킨 것을 안 한 것). 미지정(기본)은 그대로 통과한다.
            return 400, {"executed": False,
                         "note": f"unknown diagnose kind: {kind}",
                         "kinds": ["components", "communities", "community_context",
                                   "claims"]}
        return self._read(lambda r: q.component_diagnosis(
            owner=owner, store=r, member_limit=_int(p.get("member_limit"), 0) or None))

    def read_share(self, p):
        # share pack(D19) — 조회 경로로 팩 본문을 반환(파일 저장은 호출자 몫).
        return self._read(lambda r: share.build_share_pack(
            owner=_req(p, "owner"), domain=p.get("domain"), tags=_csv(p.get("tags")),
            seed=p.get("seed"), ids=_csv(p.get("ids")), depth=_int(p.get("depth"), 2),
            include_records=not p.get("no_records"), store=r))

    def read_search(self, p):
        # Phase E3 — pre-filter 푸시다운은 Chunk 실존 열(layer·doc_id)만. domain/tag
        # 는 Chunk 스키마에 열이 없어 구조적으로 불가 — P4 스키마 델타로 이관(계획 §E3).
        filters = {k: v for k, v in
                   (("layer", p.get("layer")), ("doc_id", p.get("doc"))) if v}
        query = _req(p, "query")
        qvec, vnote = self._query_embedding(query)
        # SEC-1: owner 는 선택(로컬 단일 사용자는 미지정 = 전역). 호스티드 원격은
        # MCP 가 토큰에서 해석한 owner 를 항상 실어 보내 스코프를 강제한다.
        code, res = self._read(lambda r: q.search(query,
                                                  query_embedding=qvec,
                                                  top_k=_int(p.get("top_k"), 5),
                                                  filters=filters or None,
                                                  cursor=p.get("cursor") or None,
                                                  detail=p.get("detail") or "summary",
                                                  historical=_flag(p.get("historical")),
                                                  owner=p.get("owner") or None, store=r))
        # GM-14: 무효/실효 커서는 **명시적 클라이언트 오류**다 — 조용히 첫 페이지로
        # 폴백하면 호출자가 같은 페이지를 무한히 읽는다. 본문은 그대로 돌려준다
        # (Bridge 가 HTTPError 본문을 verbatim 통과시켜 MCP 도 같은 사유를 본다).
        if isinstance(res, dict) and str(res.get("error") or "").startswith(
                ("invalid_cursor", "stale_cursor")):
            code = 400
        # **조용한 강등 금지.** 어느 팔이 돌았는지는 `q.search` 가 `arms` 로 남긴다
        # (CLI 와 같은 값이어야 §11 게이트① 동등성이 성립한다). 여기서는 **시도했는데
        # 실패한 경우에만** 사유를 덧붙인다 — 시도조차 안 한 경우(창구 미설정, 데스크탑의
        # 정상 상태)에 빈 필드를 붙이면 그 동등성이 깨진다.
        if vnote and isinstance(res, dict):
            res["vector_note"] = vnote
            # GM-13: 이행 **중일 때만** 구조화 진행도를 가산한다 — 배지는 문자열
            # 파싱이 아니라 이 필드를 본다(vector_note 의 영문 문장은 카피가 아니다).
            if self._mig_pending:
                res["embed_migration"] = {"pending": self._mig_pending}
        return code, res

    def _embed_migrating(self) -> str:
        """이행 중이면 사유 문구, 아니면 빈 문자열. **30초 캐시**한다.

        검색마다 스토어를 한 번 더 두드리면 그 자체가 조회 비용이다. 이행은 시간
        단위로 도는 일이라 30초 낡은 답이어도 판단이 바뀌지 않는다."""
        now = time.time()
        if now - self._mig_at < 30.0:
            return self._mig_note
        self._mig_at = now
        self._mig_note = ""
        self._mig_pending = 0
        try:
            r = self._reader()
            try:
                st = docstore.embed_model_state(r)
            finally:
                if r is not None:
                    r.close()
            if st.get("migrating"):
                # 화면(GM-13 배지)은 이 영문 문장을 그대로 쓰지 않는다(em 대시 포함,
                # D38) — 구조화 진행도는 _mig_pending 이 따로 나른다.
                self._mig_note = (f"embedding migration in progress "
                                  f"({st['pending']} docs pending) — FTS only")
                self._mig_pending = int(st.get("pending") or 0)
        except Exception:
            pass                      # 판단이 안 서면 막지 않는다(기본은 통과)
        return self._mig_note

    def _query_embedding(self, text: str):
        """쿼리 임베딩을 얻는다 — 없으면 `(None, 사유)` 로 **정직하게** 강등한다.

        임베딩 모델을 들고 있는 프로세스는 임베딩 워커뿐이다(사이드카엔 torch 가 없다).
        그래서 워커가 여는 창구(`packaging/embed_worker.py` `serve_encode`)에 물어본다.
        `VINC_EMBED_ENCODE` 가 없으면 **시도조차 하지 않는다** — 데스크탑에는 워커가
        없고, 매 검색마다 연결 실패를 기다리는 건 그 자체로 성능 사고다.

        상한이 둘인 이유: 이 호출은 **조회 요청 안**에 있으므로 인코더가 바쁠 때는
        빨리 포기하는 게 맞지만(`_QVEC_TIMEOUT`), 인코더가 잠들어 있을 때는 한 번
        기다려 깨우는 게 맞다(`_QVEC_COLD_TIMEOUT`) — 깨고 나면 그 뒤가 전부 싸다.
        어느 쪽인지는 `_claim_cold_retry` 가 최근 성공 시각으로 가른다. 강등은 응답
        메타(`vector_note`)에 남으므로 조용하지 않다."""
        url = os.environ.get("VINC_EMBED_ENCODE", "").strip()
        if not url or not text:
            return None, ""
        # 모델 이행 중에는 **벡터 팔을 아예 쓰지 않는다** — 반쪽 커버리지가 순위에
        # 끼면 "이미 처리된 문서" 쪽으로 결과가 기운다. FTS 로만 답하는 편이 정직하고,
        # 이행이 끝나면 저절로 풀린다([[vinc.plan.embedding-pipeline]]).
        mig = self._embed_migrating()
        if mig:
            return None, mig
        try:
            return self._encode_query(url, text, _QVEC_TIMEOUT)
        except Exception as exc:
            if not (_is_timeout(exc) and self._claim_cold_retry()):
                return None, f"{type(exc).__name__}: {str(exc)[:120]}"
        # 콜드로 판정됐다 — **한 번만** 길게 기다린다. 여기서도 실패하면 인코더가 죽은
        # 것으로 보고 쿨다운을 걸어, 뒤따르는 질의들이 같은 값을 물지 않게 한다.
        t0 = time.monotonic()
        try:
            out = self._encode_query(url, text, _QVEC_COLD_TIMEOUT)
            # 성공한 콜드 재시도는 어디에도 안 남으면 "가끔 30초 걸리는 검색"이 된다.
            # `vector_note` 는 못 쓴다 — 그 필드의 계약이 "의미 팔이 **안** 돌았을 때만
            # 나타난다"이고(`vinc_mcp/tools.py`) 여기서는 돌았다. 화면에 보이게 하는 것은
            # 별건이고, 그때까지 최소한 로그에는 남는다.
            print(f"vinc: 질의 임베딩 콜드 재시도 성공 — "
                  f"{time.monotonic() - t0:.1f}s 기다렸다", file=sys.stderr, flush=True)
            return out
        except Exception as exc:
            with self._qvec_lock:
                self._qvec_cold_until = time.monotonic() + _QVEC_COLD_COOLDOWN
            return None, f"{type(exc).__name__}: {str(exc)[:120]}"
        finally:
            with self._qvec_lock:
                self._qvec_cold_busy = False

    def _encode_query(self, url: str, text: str, timeout: float):
        """창구에 한 번 물어본다. 성공하면 `(벡터|None, 사유)`, 실패하면 **던진다** —
        바깥이 시간 초과 여부를 보고 긴 상한으로 한 번 더 갈지 정한다.

        ⚠ **토큰을 실어야 한다**(2026-08-07). 창구가 루프백일 때는 인증이 없었는데,
        모델을 `vinc-encode` 로 분리하면서 인증이 붙었다. 배치 워커만 고치고 이 경로를
        빠뜨려 **질의 임베딩이 전부 401** 이었다 — 배경 적재는 정상이라 겉으로는 다
        되는 것처럼 보였다. `vector_note` 에 사유가 남아 그나마 드러났다."""
        vecs = _encode_post(url, [text], "query", timeout)
        self._qvec_ok = time.monotonic()  # 살아 있다 — 다음 초과는 '바쁨'으로 읽는다
        return (list(vecs[0]), "") if vecs else (None, "encode returned no vector")

    def _claim_cold_retry(self) -> bool:
        """짧은 상한이 시간 초과일 때 **긴 상한으로 한 번 더** 갈 자격을 준다.

        주는 조건 셋. (1) 웜 창(`_QVEC_WARM_WINDOW`) 안에 성공한 적이 없다 — 있으면
        인코더는 살아 있고 지금 초과는 '바쁨'이라 더 기다려도 손해다. (2) 직전 긴
        시도가 실패해 건 쿨다운이 지났다 — 인코더가 죽어 있는데 **모든** 질의가 긴
        상한을 물면 안 된다. (3) 긴 상한으로 기다리는 스레드가 지금 없다 — 동시 질의가
        다같이 기다리면 콜드스타트 한 번이 요청 전부를 세운다."""
        now = time.monotonic()
        with self._qvec_lock:
            if self._qvec_cold_busy or now < self._qvec_cold_until:
                return False
            if now - self._qvec_ok < _QVEC_WARM_WINDOW:
                return False
            self._qvec_cold_busy = True
            return True

    def read_decide(self, p):
        return self._read(lambda r: q.decide(_req(p, "topic"),
                                             depth=_int(p.get("depth"), 2),
                                             owner=p.get("owner") or None, store=r))

    def read_plan(self, kind, p):
        if kind == "recall":
            return 200, q.recall_plan(p.get("query", "")).to_dict()
        if kind == "decide":
            return 200, q.decide_plan(p.get("topic", "")).to_dict()
        if kind == "search":
            return 200, {"kind": "search", "arms": ["fulltext(BM25)", "vector(주입 시)"],
                         "fusion": "RRF(k=60)", "note": "store 메서드 경유 — 단일 Cypher 아님"}
        return 400, {"error": f"unknown plan kind: {kind}"}

    # ── 쓰기 라우트(동기, 잡 큐 직렬화 R4) ─────────────────────────────────
    def write_pin(self, body):
        # SEC-3: owner 전달 → 강제 owner(원격/웹)면 소유 episode 만 pin(교차테넌트 차단).
        ep = _req(body, "episode")
        owner = body.get("owner") or None
        res = self.jobs.run_sync("pin", lambda: pref.pin(
            ep, pinned=not bool(body.get("off")), owner=owner, store=self.store))
        return 200, res

    def write_tag(self, body):
        ep = _req(body, "episode")
        owner = body.get("owner") or None
        tags = body.get("tags") or []
        if isinstance(tags, str):
            tags = _csv(tags) or []
        res = self.jobs.run_sync("tag",
                                 lambda: pref.tag(ep, list(tags), owner=owner, store=self.store))
        return 200, res

    def write_touch(self, body):
        ep = _req(body, "episode")
        owner = body.get("owner") or None
        res = self.jobs.run_sync("touch", lambda: pref.touch(
            ep, now=body.get("now"), owner=owner, store=self.store))
        return 200, res

    def write_activate(self, body):
        # activate 는 DB read + 파일 write(팩) — 조회 경로(reader)로 처리.
        return self._read(lambda r: act.activate(
            body.get("query", ""), owner=_req(body, "owner"), domain=body.get("domain"),
            tenant=body.get("tenant"), workspace=body.get("workspace"),
            rank=body.get("rank", "preference"), tags=body.get("tags"),
            limit=_int(body.get("limit"), 20), include_model=not bool(body.get("no_model")),
            generated_at=body.get("now"), out_dir=body.get("out"), store=r))

    def write_ingest(self, body):
        # 적재 단일화 — `/ingest` 는 fragment 단일 경로로 수렴한다. 개방층은 그 경로에
        # 포팅돼 revstamp·SEC-3·dedupe 를 승계한다(`engines/fragment_ingest.py` 는
        # 계보 참조용, 호출부 없음 · [[vinc.spec.dataplane §3]]).
        cap = _pop_max_domains(body)
        res = self.jobs.run_sync("ingest:fragment",
                                 lambda: frag.merge_fragment(body, store=self.store,
                                                             themes=self.themes,
                                                             max_domains=cap,
                                                             read_back=True))
        return 200, res

    # ── 계정 연결(디바이스 연결 플로우 — 회원 키 복붙 종식) ─────────────────────
    # RFC 8252(네이티브 앱 OAuth) 변형: 앱이 state 논스를 발급받아(start, 인증) 시스템
    # 브라우저로 vincs.app/connect-app/#port&state 를 연다. 로그인·승인·키 발급은 전부
    # 브라우저(계정 오리진)에서 일어나고, 페이지가 발급된 vinc_sk_* 를 루프백 콜백
    # (무인증 — state 단일사용 게이트)으로 전달하면 앱이 status(인증)로 1회 수령한다.
    # B1 보존: 앱은 계정 API 를 호출하지 않고 세션도 만들지 않는다 — 키만 받는다.
    # 위협 모델: state 는 256bit 난수·TTL 10분·단일사용이라 드라이브바이 로컬 POST 로
    # 위조 불가. 로컬 악성 프로세스가 브라우저 argv 에서 state 를 훔치는 경로는 존재
    # 하나, 그 수준의 로컬 침해는 localStorage 의 키 자체를 읽을 수 있어 등가 위협.
    _CONNECT_TTL = 600.0

    def write_connect_start(self, body):
        state = secrets.token_urlsafe(32)
        now = time.monotonic()
        with self._connect_lock:
            # 만료·사용済 청소 — 진행 중 요청은 최신 start 가 대체한다(동시 1개면 충분).
            self._connect = {s: v for s, v in self._connect.items()
                             if v["expires"] > now and "key" not in v}
            self._connect[state] = {"expires": now + self._CONNECT_TTL}
        return 200, {"state": state, "expires_in": int(self._CONNECT_TTL)}

    def connect_callback(self, body, origin):
        """무인증(브라우저 호출) — 인증은 state 논스가 대신한다. Origin 은 심층방어
        (브라우저 교차오리진 POST 는 항상 Origin 을 실음; curl 등 무 Origin 은 state
        게이트만으로 충분)."""
        if origin and origin not in connect_origins():
            return 403, {"error": "origin not allowed"}
        state = (body.get("state") or "").strip()
        key = (body.get("key") or "").strip()
        email = (body.get("email") or "").strip()
        # username(핸들)은 표시 전용(009) — 없으면 빈 문자열. 길이만 방어적으로 클램프,
        # 형식 검증은 계정 백엔드가 이미 함(여긴 그대로 실어 나르기만).
        username = (body.get("username") or "").strip()
        if not state or not key.startswith("vinc_sk_") or len(key) > 200:
            return 400, {"error": "invalid payload"}
        now = time.monotonic()
        with self._connect_lock:
            ent = self._connect.get(state)
            if not ent or ent["expires"] <= now or "key" in ent:
                return 400, {"error": "unknown, used or expired state"}
            ent["key"] = key
            ent["email"] = email[:200]
            ent["username"] = username[:20]
        return 200, {"status": "ok"}

    def read_connect_status(self, p):
        state = _req(p, "state")
        now = time.monotonic()
        with self._connect_lock:
            ent = self._connect.get(state)
            if not ent or ent["expires"] <= now:
                self._connect.pop(state, None)
                return 200, {"status": "expired"}
            if "key" not in ent:
                return 200, {"status": "pending"}
            self._connect.pop(state, None)   # 1회 전달 후 즉시 소거(메일박스)
            return 200, {"status": "delivered", "key": ent["key"],
                         "email": ent.get("email", ""), "username": ent.get("username", "")}

    # ── 삭제 / 병합 라우트 (T3c — 오염 정리 쓰기 표면, R4 직렬화) ────────────────
    # 뮤테이션이므로 잡 큐(run_sync)로 단일 writer 에 직렬화한다(pin/tag/ingest 동형).
    # 둘 다 **비가역** — DETACH DELETE. writer store(self.store)로 실행.
    def write_delete(self, body):
        # SEC-3: owner 를 엔진에 전달 → owner 주어지면 owner_id 스코프 삭제(교차테넌트
        # 봉쇄). 로컬 앱은 owner 미전송 → None → 무스코프(현행). 원격/웹은 강제 owner.
        node = _req(body, "node")
        owner = body.get("owner") or None
        res = self.jobs.run_sync("delete",
                                 lambda: q.delete_node(node, owner=owner, store=self.store))
        return 200, res

    def write_delete_edge(self, body):
        # S4 확장 — 엣지 1개 삭제 + 엣지 툼스톤(삭제 전파). SEC-3: owner 주어지면
        # 양 끝점 owner 스코프(delete 동형 — 로컬 앱은 owner 미전송 → 무스코프).
        src, dst = _req(body, "from"), _req(body, "to")
        etype = _req(body, "type")
        owner = body.get("owner") or None
        res = self.jobs.run_sync("delete_edge",
                                 lambda: q.delete_edge(src, etype, dst, owner=owner,
                                                       store=self.store))
        return 200, res

    def write_merge(self, body):
        # SEC-3: owner 전달 → merge_nodes 가 keep·drop 소유 일치 시에만 병합(교차테넌트 봉쇄).
        keep, drop = _req(body, "keep"), _req(body, "drop")
        owner = body.get("owner") or None
        res = self.jobs.run_sync("merge_nodes",
                                 lambda: q.merge_nodes(keep, drop, owner=owner, store=self.store))
        return 200, res

    def write_merge_reject(self, body):
        # F10-b 기각 기억 — "이 둘은 다른 것" 판정 기록(가역: undo=true 로 취소).
        # 병합(비가역)과 짝을 이루는 **안전한 쪽** 행동이라 같은 쓰기 큐로 직렬화한다.
        # owner 는 query 층이 필수로 강제한다(미전달 시 executed=false + error).
        a, b = _req(body, "a"), _req(body, "b")
        owner = body.get("owner") or ""
        res = self.jobs.run_sync("merge_reject", lambda: q.reject_merge_pair(
            owner=owner, a_id=a, b_id=b, note=body.get("note") or "",
            undo=bool(body.get("undo")), store=self.store))
        return 200, res

    # ── 문서 정본(Chunk=SSoT, D45 P4) — 직접 수정 표면 백엔드 ──────────────────
    # 편집 UX 의 백엔드: get→편집→put 왕복. 쓰기는 잡 큐로 단일 writer 직렬화(재청킹·
    # 인덱스 drop 이 다른 쓰기와 경합하지 않게). ⚠ 여기는 정본 텍스트 CRUD 만 노출한다 —
    # 파생 노드 재도출 정책은 별개다([[vinc.canon.chunk-ssot]]).
    def read_doc(self, p):
        return self._read(lambda r: docstore.get_doc(r, _req(p, "id"),
                                                     owner=p.get("owner") or None))

    def read_node_context(self, p):
        # D45-V — 뷰 노드 클릭 상세: 노드 설명(one_liner/summary) + 정본 근거 발췌.
        return self._read(lambda r: q.node_context(
            owner=_req(p, "owner"), node=_req(p, "node"),
            limit=_int(p.get("limit"), 4), store=r))

    def read_brief(self, p):
        # W1 — 질문 하나로 답변 재료(본문·발췌·관계·근거 id)를 한 번에 낸다.
        # 임베딩 창구는 `/search` 와 같은 것을 쓴다: 해소가 같은 후보 위에서 돌아야
        # 데스크탑 Ask 와 답이 갈리지 않는다. 근거: [[vinc.plan.mcp-agent-surface §4]]
        query = _req(p, "query")
        qvec, vnote = self._query_embedding(query)
        # 유효구간 기본은 current 만이다(채택된 시간 정책 §4.2). `historical=true` 가
        # 명시적 요청 경로이고, 기본으로 켜 두면 정책이 막으려는 것을 그대로 낸다.
        code, res = self._read(lambda r: q.brief(
            query, owner=_req(p, "owner"), query_embedding=qvec,
            max_excerpts=_int(p.get("max_excerpts"), 3),
            historical=_flag(p.get("historical")), store=r))
        # **시도했는데 실패한 경우에만** 사유를 덧붙인다(`/search` 와 같은 규칙 —
        # 시도조차 안 한 경우에 빈 필드를 붙이면 게이트① 동등성이 깨진다).
        # `arms` 는 여기서 붙이지 않는다 — `q.brief` 가 내부 search 에서 그대로 나른다
        # (CLI 와 같은 값이어야 하므로 HTTP 계층에 두면 안 된다).
        if vnote and isinstance(res, dict):
            res["vector_note"] = vnote
        return code, res

    def read_doc_highlights(self, p):
        # D45-V2 2단계 — Reader 전문/발췌 내 개념 언급 위치(밑줄 char 범위).
        return self._read(lambda r: docstore.doc_highlights(
            r, _req(p, "doc_id"), _req(p, "concept_id"),
            owner=p.get("owner") or None))

    def read_doc_context(self, p):
        # 리더가 본문 **앞에** 내놓는 것: 이 문서가 무엇에 걸려 있고(개념 칩) 어떤
        # 모양인가(목차), 그리고 검색의 청크 히트가 착지할 오프셋.
        return self._read(lambda r: docstore.doc_context(
            r, _req(p, "doc_id"), owner=p.get("owner") or None))

    def read_edge_context(self, p):
        # D45-V 1단계 — 흐름 뷰 선 클릭: 관계의 evidence·confidence + 양끝 발췌.
        return self._read(lambda r: q.edge_context(
            owner=_req(p, "owner"), src=_req(p, "src"), dst=_req(p, "dst"),
            etype=_req(p, "type"), store=r))

    def read_ledger_drilldown(self, p):
        # D45-V2 3단계 — 합계 생키 구간 클릭 → 그 구간의 개념 목록(원장 수치 정합).
        return self._read(lambda r: q.ledger_drilldown(
            owner=_req(p, "owner"), domain=_req(p, "domain"), mark=_req(p, "mark"),
            seed=p.get("seed") or None, depth=_int(p.get("depth"), 2), store=r))

    def read_docs_in_range(self, p):
        # D45-V2 4단계 — 타임라인 기간 브러시: [start, end) 구간의 날짜 있는 항목.
        # ⚠ basis 는 차트가 보낸 값을 그대로 받아야 한다 — 밴드를 눌러 여는 목록이
        # 다른 시간 열로 세면 화면이 스스로를 반박한다(D72 · docs_in_range 독스트링).
        return self._read(lambda r: q.docs_in_range(
            owner=_req(p, "owner"), start=_req(p, "start"), end=_req(p, "end"),
            domain=p.get("domain") or None, limit=_int(p.get("limit"), 200),
            basis=p.get("basis") or q.BASIS_RECORDED, store=r))

    def read_docs(self, p):
        # F5 — limit/cursor/detail 은 그대로 통과시킨다. **기본은 종전(전량·full)**:
        # nav_tree·CLI·앱이 이 경로를 그대로 쓰고 있어 기본을 바꾸면 조용히 깨진다.
        # 잘라 달라고 말한 쪽(MCP)만 잘린 응답을 받는다.
        lim = _int(p.get("limit"), 0) or None

        def _list(r):
            try:
                return docstore.list_docs(
                    r, owner=p.get("owner") or None, limit=lim,
                    cursor=p.get("cursor") or None,
                    detail=(p.get("detail") or "full"))
            except ValueError as exc:
                # 무효 커서는 500 이 아니라 사유 있는 거절이다. 본문 모양은 검색의
                # 무효 커서와 같다(executed:false + error) — MCP 층이 그 문장으로
                # 같은 인자 거절 봉투를 만든다.
                if not str(exc).startswith("invalid_cursor"):
                    raise
                return {"kind": "doc.list", "executed": False, "docs": [], "count": 0,
                        "error": str(exc), "note": str(exc)}

        return self._read(_list)

    def read_changes(self, p):
        """변경 표식(VIN-129). 스토어를 안 읽는다: 폴링이 단일 writer 에 짐을 얹지 않게
        하려고 쓰기 관문에서 센 값을 메모리에서 준다. 그래서 스토어가 죽어도 답한다."""
        return 200, {"executed": True, "marker": self.changes.read(p.get("owner"))}

    def note_write(self, path: str, owner, code: int, obj) -> None:
        """POST 한 건이 끝났다. 성공한 쓰기면 그 소유자의 표식을 올린다.

        비동기 잡(202)은 접수가 아니라 **끝**을 센다(`ChangeMarker.bump_when_done`).
        동기화 세션은 무엇이든 들였을 때만 센다: 10분마다 도는 빈 세션까지 세면 열린
        화면이 까닭 없이 다시 그려진다."""
        if code >= 400 or not changes.counts(path):
            return
        owner = owner if isinstance(owner, str) and owner else None
        if code == 202 and isinstance(obj, dict) and obj.get("job_id"):
            pred = changes.sync_brought_something if path == "/sync" else None
            self.changes.bump_when_done(owner, self.jobs.get(str(obj["job_id"])), pred)
            return
        self.changes.bump(owner)

    def read_conventions(self, p):
        """관례(GRAPH.md) 조회 — 어시스턴트가 **쓰기 전에** 읽는 채널([[vinc.canon.graph-conventions]] §6.2).

        스토어에 관례 문서가 있으면 그 본문을, 없으면 **생성 구역만 채운 기본 템플릿**을
        돌려준다. 사용자가 아무것도 안 해 뒀어도 도메인·재가 어휘는 전달된다 — 이 기본본이
        없으면 '아직 안 만든 사람'에게는 이 도구가 그냥 빈손이고, 그 경우가 대다수다.

        ⚠ **저장본의 생성 구역은 매번 다시 계산해서 내보낸다**(2026-08-22). 여태 저장된
        본문을 글자 그대로 돌려줬는데, 생성 구역은 스토어의 사본이라 마지막 build 이후
        도메인이 늘거나 어휘가 재가되면 그만큼 낡는다 — 실측: 2026-08-12 에 build 된
        블록이 `vinc_domains` 대비 약 45% 어긋나 있었다. 이 도구는 **쓰기 직전에**
        읽히는 자리라 그 낡음이 곧 잘못 쓰인 도메인이 된다. 산문과 저장된 built 날짜는
        건드리지 않는다(날짜는 라벨이다 — 언제 사람이 이 파일을 만들었나)."""
        owner = p.get("owner") or None
        lang = p.get("lang") or None

        def _run(r):
            # 관례 id 는 owner 마다 다르다 — 구 고정 id 를 이미 쥔 owner 만 그 자리를
            # 유지한다(docstore.resolve_conventions_doc_id). 응답의 `doc_id` 는 앱의
            # 저장 경로가 그대로 쓰는 값이라, 여기서 정한 것이 곧 쓰기 대상이다.
            doc_id = docstore.resolve_conventions_doc_id(r, owner)
            got = docstore.get_doc(r, doc_id, owner=owner)
            # 이 도구는 규율상 **세션 첫 쓰기 전에** 부르는 자리다(CLAUDE.md).
            # 그래서 정체를 여기 실으면 따로 묻지 않아도 필요한 순간에 도착한다 —
            # whoami 를 하나 더 만드는 대신 이미 첫 번째인 문에 붙인다.
            # `scope` 는 그 owner 가 **개인 그래프인지 팀 그래프인지**를 말한다 —
            # owner 문자열만으로는 접두를 아는 사람만 읽을 수 있었다.
            # 근거: [[decision:identity-rides-on-the-first-door-not-a-whoami]]
            if got.get("executed") and (got.get("text") or "").strip():
                # 마커가 깨진 문서는 교체하지 않는다(build 가 executed=False) — 그때는
                # 저장본 그대로다. 추측해서 고쳐 쓰지 않는 것이 build 와 같은 태도다.
                fresh = graphmd.build(r, owner=owner, themes=self.themes,
                                      existing=got["text"], restamp=False)
                text = fresh["text"] if fresh.get("executed") else got["text"]
                return {"kind": "conventions", "executed": True, "source": "doc",
                        "owner": owner or "", "scope": _owner_scope(owner),
                        "doc_id": doc_id, "text": text,
                        "generated_recomputed": bool(fresh.get("executed")),
                        "updated_at": got.get("updated_at") or ""}
            built = graphmd.build(r, owner=owner, themes=self.themes, lang=lang,
                                  day=_dt.date.today().isoformat())
            return {"kind": "conventions", "executed": True, "source": "default",
                    "owner": owner or "", "scope": _owner_scope(owner),
                    "missing": True, "doc_id": doc_id,
                    "text": built.get("text") or ""}
        return self._read(_run)

    def read_nav_tree(self, p):
        # D69 — 좌측 문서 트리: 주제 → 문서(개념 계층 깊이순) + 미분류.
        # `nodes=1` 이면 주제마다 노드 목록도 싣는다(좌측 트리의 노드 갈래). 기본은
        # 꺼짐이라 이 경로를 이미 쓰는 곳의 응답은 바뀌지 않는다.
        # `kind=` 는 그 종 **하나**로 모집단을 좁힌다(종 레일). 목록을 종별로 가져가는
        # 것이 계약이라 여기에 "전부" 값은 없다 - 개수는 counts.kinds 가 따로 준다.
        return self._read(lambda r: q.nav_tree(
            owner=_req(p, "owner"), nodes=_flag(p.get("nodes")),
            kind=p.get("kind"), store=r))

    def write_doc_put(self, body):
        owner = body.get("owner") or None
        if not owner:
            return 400, {"error": "owner required — 정본은 무소유로 두지 않는다(SEC-1)"}
        # content_time(D72)은 **키가 없을 때와 빈 값일 때가 다르다**: 키가 없으면
        # 기존 값을 보존하고, 빈 문자열이면 사용자가 지운 것이다. None 으로 뭉개면
        # 제목만 고쳐도 내용 시점이 되살아난다.
        ctime = body.get("content_time")
        # title 도 같은 3값이다. 여기서 `or ""` 로 뭉개면 **본문만 재전송한 호출이
        # 제목을 지운다** — 그것이 라이브 문서 여덟 건의 제목을 비운 경로였다.
        btitle = body.get("title")
        res = self.jobs.run_sync("doc.put", lambda: docstore.put_doc(
            self.store, owner=owner, text=_req(body, "text"),
            doc_id=body.get("id") or None,
            title=None if btitle is None else str(btitle),
            origin=body.get("origin") or "",
            content_time=None if ctime is None else str(ctime),
            # 주제는 **적는 값**이다. `or None` 으로 뭉개도 안전하다 — 빈 문자열은
            # "지운다"가 아니라 "안 건드린다"이고, 그 판단은 _set_authored_topic 에 있다.
            topic=body.get("topic") or None))
        return 200, res

    def write_conventions_build(self, body):
        """관례 재생성 — 앱의 [다시 생성]. **산문은 건드리지 않는다**(graphmd.build).

        기존 문서가 있으면 그 생성 구역만 새로 쓰고, 없으면 템플릿을 만들어 저장한다.
        마커가 깨진 문서는 교체하지 않고 오류로 돌린다 — 어디였는지 추측하지 않는다."""
        owner = body.get("owner") or None
        if not owner:
            return 400, {"error": "owner required — 정본은 무소유로 두지 않는다(SEC-1)"}
        lang = body.get("lang") or None

        def _job():
            doc_id = docstore.resolve_conventions_doc_id(self.store, owner)
            got = docstore.get_doc(self.store, doc_id, owner=owner)
            existing = got.get("text") if got.get("executed") else None
            built = graphmd.build(self.store, owner=owner, themes=self.themes,
                                  existing=existing, lang=lang,
                                  day=_dt.date.today().isoformat())
            if not built.get("executed"):
                return built
            put = docstore.put_doc(self.store, owner=owner, text=built["text"],
                                   doc_id=doc_id, title="GRAPH.md",
                                   origin=graphmd.CONVENTIONS_ORIGIN)
            return {"kind": "conventions.build", "executed": True, "text": built["text"],
                    "replaced": bool(built.get("replaced")), "doc": put}

        res = self.jobs.run_sync("conventions.build", _job)
        return (200, res) if res.get("executed") else (400, res)

    def write_doc_put_file(self, body):
        """파일 바이트 → 원문 텍스트 → 정본 적재. `/doc/put` 의 바이너리 입구다.

        **왜 base64-in-JSON 인가**: 계정 프록시가 상태변경 요청에 `Content-Type:
        application/json` 을 강제해(guards.ts) multipart 는 웹 경로에서 원천 거부되고,
        `_body()` 도 json.loads 고정이다. 4/3 팽창을 감수하고 한 경로로 통일한다 —
        웹은 어차피 허브 MAX_BODY(4MB)가 앞서 걸리므로 실효 상한이 결정한다.

        추출 실패(미지원 형식·암호화·스캔 PDF·추출기 부재)는 500 이 아니라
        **executed=False + 안정 코드**로 돌린다: 호출부가 읽히는 문구로 바꾼다."""
        owner = body.get("owner") or None
        if not owner:
            return 400, {"error": "owner required — 정본은 무소유로 두지 않는다(SEC-1)"}
        filename = body.get("filename") or ""
        try:
            data = base64.b64decode(body.get("data_b64") or "", validate=True)
        except Exception:
            return 400, {"error": "data_b64 must be valid base64"}
        if not data:
            return 400, {"error": "empty file"}
        got = textextract.extract_text(filename, data)
        if not got.get("executed"):
            return 200, got                       # 강등 — 사유 코드를 그대로 전달
        title = body.get("title") or os.path.splitext(os.path.basename(filename))[0]
        res = self.jobs.run_sync("doc.put-file", lambda: docstore.put_doc(
            self.store, owner=owner, text=got["text"],
            doc_id=body.get("id") or None, title=title,
            origin=body.get("origin") or filename))
        res["source_format"] = got.get("format") or ""
        return 200, res

    def write_doc_delete(self, body):
        res = self.jobs.run_sync("doc.delete", lambda: docstore.delete_doc(
            self.store, _req(body, "id"), owner=body.get("owner") or None))
        return 200, res

    def write_doc_reembed(self, body):
        # D45-E 후속3 — 앱 편집으로 벡터가 빠진 문서(needs_embed)의 임베딩을 채운다.
        # 임베딩 스택(chonkie/sentence-transformers)이 없으면 조용히 note 강등(FTS 는
        # 즉시 서빙되므로 무해). id 지정 = 그 문서, 미지정 = owner 의 stale 전부.
        owner = body.get("owner") or None
        did = body.get("id") or None

        def _run():
            try:
                from engines.embed_pipeline import build_embeddings_model, _to_list, model_dim
            except Exception as exc:
                return {"kind": "doc.reembed", "executed": False,
                        "note": f"embedding stack unavailable: {type(exc).__name__}"}
            try:
                model = build_embeddings_model("local-en")
                from chonkie import EmbeddingsRefinery
                from chonkie.types import Chunk as _CK
                refinery = EmbeddingsRefinery(embedding_model=model)
                def embed(texts):
                    cks = [_CK(text=t, start_index=0, end_index=len(t), token_count=0) for t in texts]
                    return [_to_list(e.embedding) for e in refinery(cks)]
            except Exception as exc:
                return {"kind": "doc.reembed", "executed": False,
                        "note": f"embedding model load failed: {type(exc).__name__}"}
            targets = [did] if did else docstore.stale_docs(self.store, owner=owner)
            done = 0
            for t in targets:
                r = docstore.reembed_doc(self.store, t, owner=owner, embed=embed)
                if r.get("executed"):
                    done += 1
            if targets:
                # ⚠ `build_search_indexes` 로 되돌리지 말 것 — 이 경로는 벡터만
                # 드롭하는데 그 함수는 'already exists' 를 흡수하지 않아 살아 있는 FTS
                # 위에서 던진다. 게다가 벡터 색인이 **성공한 뒤** 던져서, 다 만들어
                # 놓고 결과만 오류가 됐다([[vinc.ops.fts-index-drift]] §3).
                self.store.ensure_search_indexes(dim=model_dim("local-en"))
            return {"kind": "doc.reembed", "executed": True, "docs": done,
                    "total": len(targets)}

        return 200, self.jobs.run_sync("doc.reembed", _run)

    def write_doc_extract(self, body):
        # D45-V — 정본→노드 파생. apply=false(기본) = 후보 제안(옵션2 확인 UI 의
        # 데이터원), apply=true = 반영(옵션1 결정론 또는 승인된 후보). candidates
        # 를 실으면 그 목록을 반영(LLM/사용자 다듬은 후보 — id 는 서버 재파생).
        owner = body.get("owner") or None
        if not owner:
            return 400, {"error": "owner required — 추출·반영은 소유 스코프(SEC-1)"}
        res = self.jobs.run_sync("doc.extract", lambda: docstore.extract_doc(
            self.store, _req(body, "id"), owner=owner,
            apply=bool(body.get("apply")), candidates=body.get("candidates")))
        return 200, res

    def write_evidence_backfill(self, body):
        # U2 — 관계 근거(E-G6)의 **생산자**. 열만 있고 채우는 곳이 없어 관계 패널이
        # 언제나 "근거 없음"이었다. 정본 청크에서 두 개념이 같은 문장에 나온 자리를
        # 결정론으로 찾는다(LLM 없음). 기존 근거는 안 건드려 반복 실행이 안전하다.
        owner = body.get("owner") or None
        if not owner:
            return 400, {"error": "owner required — 근거 저작은 소유 스코프(SEC-1)"}
        res = self.jobs.run_sync("evidence.backfill", lambda: docstore.backfill_evidence(
            self.store, owner=owner, doc_id=body.get("doc_id") or None))
        return 200, res

    def write_domain_rename(self, body):
        # S2 — 도메인 일괄 변경/재배치. new='' 는 미분류로 내림(체감상 "도메인 삭제").
        # 전역 Domain 노드는 불변 — owner 노드의 domain 문자열만 바뀐다(query 주석).
        owner = body.get("owner") or None
        if not owner:
            return 400, {"error": "owner required — 도메인 변경은 소유 스코프(SEC-1)"}
        res = self.jobs.run_sync("domain.rename", lambda: q.rename_domain(
            owner=owner, old=_req(body, "old"), new=body.get("new") or "",
            store=self.store))
        return 200, res

    # ── 자체호스팅 임베딩(B) — 허브 워커 왕복. 원문은 스토어 밖으로 안 나간다 ──────
    # 사이드카 바이너리는 임베딩 스택을 번들에서 제외(2.7GB 회피)하므로, 허브의
    # 로컬 워커(sentence-transformers)가 이 두 경로로 임베딩만 채운다. **루프백 내부
    # 표면** — _PROXY_OPS 미등록·Caddy 미노출(웹/원격 클라가 못 부른다).
    def read_embed_pending(self, p):
        """워커가 일감을 가져가는 자리. **모델 전환 감지도 여기서** 한다.

        워커는 자기가 어떤 모델을 들었는지 안다(`model` 파라미터로 알려 준다). 스토어에
        기록된 모델과 다르면 전량 재임베딩을 건다 — 그러지 않으면 모델을 바꿔도
        `needs_embed` 가 안 켜져 **아무 일도 일어나지 않고**, 옛 모델 벡터가 새 쿼리
        벡터와 다른 공간에서 비교된다(에러 없이 검색만 나빠지는 그 실패 양식).

        전환은 쓰기라 잡 큐로 보낸다(R4). 멱등이라 매 라운드 불려도 첫 번째만 일한다.

        ⚠ **모델이 같으면 잡을 걸지 않는다.** 잡 큐는 끝난 잡을 전부 쓰기로 세어 스냅샷을
        예약하므로, 5분마다 오는 이 호출이 아무것도 안 바꾸고도 DB 전체(수백 MB)를
        공유 볼륨으로 복사시켰다. 리더로 기록된 모델만 먼저 읽고, 다를 때만 쓰기로 간다.
        근거: [[record:hub-snapshots-the-whole-database-every-five-minutes-because-a-no-op-job-counts-as-a-write-2026-09-25]]"""
        model = (p.get("model") or "").strip()
        if model and self._stored_embed_model() != model:
            try:
                self.jobs.run_sync(
                    "embed.model", lambda: docstore.switch_embed_model(self.store, model),
                    timeout=120.0)
            except Exception as exc:      # 전환 실패가 임베딩 자체를 막지는 않는다
                print(f"vinc: embed model switch failed — {exc}", file=sys.stderr)
        return self._read(lambda r: {
            "kind": "embed.pending",
            "chunks": docstore.pending_embed_chunks(r, limit=_int(p.get("limit"), 256))})

    def _stored_embed_model(self) -> str | None:
        """리더로 기록된 모델만 읽는다. 스토어가 없거나 못 읽으면 ``None``."""
        r = self._reader()
        if r is None:
            return None
        try:
            return docstore.stored_embed_model(r)
        finally:
            r.close()

    def embed_health(self) -> dict:
        """`/health` 에 실을 임베딩 백로그 진단 — **정체 자체가 알람이 되게** 한다.

        이 프로젝트가 반복해 겪는 실패 모양은 늘 같다: 기동 정상 · `/health` 200 ·
        기능만 죽음. 임베딩도 그 목록에 두 번 올랐다(Azure 이관 때 워커가 통째로
        사라진 것, 그리고 2026-08-05~07 에 워커가 기동 직후 죽어 38시간 방치된 것).
        두 번 다 **밖에서 볼 수 있는 수치가 없어서** 늦게 발견됐다.

        그래서 세 값을 낸다: `pending`(남은 문서) · `last_applied_at`(워커가 마지막으로
        실제 일한 시각) · `stored`(색인을 만든 모델). pending 이 있는데 last_applied_at
        이 오래됐으면 워커가 죽은 것이다 — 그 판정을 사람이나 모니터가 할 수 있다.

        읽기는 **리더 뷰**로 한다. writer 커넥션을 쓰면 긴 적재 잡이 도는 동안 헬스가
        매달리고, 엔트리포인트의 헬스 폴링(1초 간격)이 타임아웃 나 워치독이 사이드카를
        재기동시킨다 — 진단이 장애를 만드는 모양이 된다."""
        r = self._reader()
        if r is None:
            return {"error": "no reachable store"}
        return docstore.embed_model_state(r)

    def write_node_mirror_rebuild(self, body):
        """POST /node-mirror/rebuild — 노드 미러를 통째로 다시 만든다.

        **배포 경계에서만 부른다**(`packaging/docker-entrypoint.sh`). 증분 삽입은
        기각됐다 — "없던 벡터가 생기는" HNSW 삽입만 세그폴트를 내기 때문이고, 그래서
        미러는 전량 재구축 아니면 아무것도 아니다. 그 비용(노드 수에 비례, 실측 1365
        노드에 약 6분)을 **질의 경로가 아니라 릴리스에 물린다**. 평시 노드 쓰기는 미러
        지연을 허용한다 — 그동안에도 제목 팔과 그래프 팔은 그대로 산다.
        근거: [[decision:the-semantic-arm-ships-on-and-the-mirror-rebuilds-at-the-release-boundary]]
        · [[record:the-node-mirror-rebuilds-at-the-deploy-boundary-2026-09-10]]

        ⚠ **인코딩을 쓰기 큐 밖에서 한다.** 전부를 한 잡으로 묶으면 그 몇 분 동안
        모든 쓰기가 단일 워커 뒤에 줄을 서고, `run_sync` 의 상한(600초)을 넘기는 순간
        기다리던 쓰기가 **지연이 아니라 500** 이 된다. 느린 것은 인코더 왕복이고
        그것은 스토어를 안 만지므로, 여기서 다 받아 두고 마지막의 짧은 교체만 큐로
        보낸다. 조회는 리더 커넥션이라 어느 쪽에도 안 막힌다.

        중간에 죽으면 인덱스 없는 미러가 남는다. 그 상태에서 의미 팔은 빈 목록을 낼
        뿐이라 검색은 멀쩡하고, 다음 배포가 다시 만든다."""
        url = os.environ.get("VINC_EMBED_ENCODE", "").strip()
        if not url:
            # 데스크탑에는 인코더가 없다 — 미러도 없고, 의미 팔은 빈 목록을 낸다.
            return 503, {"executed": False,
                         "error": "VINC_EMBED_ENCODE 가 비어 있다 — 인코더 없이는 못 만든다"}
        if not self._mirror_lock.acquire(blocking=False):
            return 409, {"executed": False, "error": "미러 재구축이 이미 돌고 있다"}
        try:
            r = self._reader()
            if r is None:
                return 503, {"executed": False, **store_unavailable()}
            # 🔴 **리더를 여기서 닫는다.** 인코딩 몇 분 동안 열어 두면 그 커넥션이 읽기
            # 트랜잭션을 쥐고, 뒤이은 교체의 `CREATE_VECTOR_INDEX`(=DDL=쓰기)가
            # `Cannot start a new write transaction` 으로 거절된다. 그 자리는 멱등을
            # 위해 예외를 삼키므로 **행은 들어가고 인덱스만 없는** 미러가 남고, 의미
            # 팔은 예외 없이 빈 목록만 낸다 — 2026-09-10 라이브에서 실제로 그랬다.
            # `_read` 가 `finally: r.close()` 인 것과 같은 이유다.
            try:
                owner = (body.get("owner") or "").strip() or None
                rows, collisions = node_mirror.collect(r, owner)
                try:
                    model = str((docstore.embed_model_state(r) or {}).get("stored") or "")
                except Exception:
                    model = ""    # 라벨일 뿐이다 — 못 읽었다고 재구축을 접지 않는다
            finally:
                r.close()
            if not rows:
                return 200, {"kind": "node-mirror.rebuild", "executed": False, "rows": 0,
                             "note": "미러에 담을 노드가 0건이다"}
            t0 = time.monotonic()
            for i in range(0, len(rows), _MIRROR_BATCH):
                batch = rows[i:i + _MIRROR_BATCH]
                vecs = _encode_post(url, [b["text"] for b in batch], "passage",
                                    _MIRROR_ENCODE_TIMEOUT)
                if len(vecs) != len(batch):
                    # 반만 받은 벡터로 미러를 갈아 끼우면 나머지 노드가 조용히 사라진다.
                    return 502, {"executed": False, "error": (
                        f"인코더가 {len(batch)}건 중 {len(vecs)}건만 돌려줬다 "
                        f"(배치 {i // _MIRROR_BATCH + 1})")}
                for b, v in zip(batch, vecs):
                    b["emb"] = list(v)
            encode_s = time.monotonic() - t0
            res = self.jobs.run_sync(
                "node-mirror.replace",
                lambda: node_mirror.replace(self.store, rows, model),
                timeout=_MIRROR_REPLACE_TIMEOUT)
        finally:
            self._mirror_lock.release()
        # `index_ok` 가 거짓이면 행은 들어갔고 인덱스만 없다 — 의미 팔이 조용히 죽는
        # 상태다. 결과에 실어야 엔트리포인트 로그와 배포 로그에 그대로 나온다.
        return 200, {"kind": "node-mirror.rebuild", "executed": True,
                     "rows": res.get("rows"), "owners": res.get("owners"),
                     "index_ok": res.get("index_ok"),
                     "index_error": res.get("index_error"),
                     # id 가 겹친 노드는 그래프의 결함이지 재구축의 결함이 아니다.
                     # 걸러 내고 **말한다** — 조용히 버리면 결함이 미러 뒤에 숨는다.
                     "id_collisions": collisions[:5],
                     "id_collision_count": len(collisions), "model": model,
                     "encode_s": round(encode_s, 1),
                     "swap_s": round(time.monotonic() - t0 - encode_s, 1),
                     "wall_s": round(time.monotonic() - t0, 1)}

    def write_embed_apply(self, body):
        # 운영자 스위치: 켜져 있으면 벡터를 안 넣는다. 백로그(needs_embed)는 그대로
        # 남아 스위치를 끄면 이어서 채워진다. 검색은 이미 있는 벡터로 계속 돈다.
        # 근거: [[record:hub-segfault-loop-on-embedding-apply-2026-09-24]]
        if embed_apply_paused():
            return 503, {"executed": False, "error": "embedding_apply_paused"}
        doc_id = _req(body, "doc_id")
        embeddings = body.get("embeddings") or {}
        res = self.jobs.run_sync("embed.apply", lambda: docstore.apply_embeddings(
            self.store, doc_id, embeddings))
        return 200, res

    # ── 주제·타입 어휘 라우트 (어휘 write 표면 — 그래프와 분리, R4 직렬화) ────────
    def read_themes(self, p):
        return 200, {"active_theme_id": self.themes.active_theme_id(),
                     "themes": self.themes.list_themes()}

    def read_types(self, p):
        theme_id = _req(p, "theme_id")
        kind = p.get("kind") or None
        status = p.get("status") or None
        return 200, {"theme_id": theme_id,
                     "types": self.themes.list_types(theme_id, kind=kind, status=status)}

    def write_theme_create(self, body):
        theme_id = _req(body, "theme_id")
        gq = body.get("guiding_questions") or []
        res = self.jobs.run_sync("theme_create", lambda: self.themes.create_theme(
            theme_id, body.get("title", ""), list(gq)))
        return 200, {"theme": res}

    def write_theme_activate(self, body):
        theme_id = _req(body, "theme_id")
        ok = self.jobs.run_sync("theme_activate", lambda: self.themes.activate_theme(theme_id))
        if not ok:
            return 404, {"error": f"no such theme: {theme_id}"}
        return 200, {"active_theme_id": theme_id}

    # 어휘 뮤테이션은 잡 큐(run_sync)로 직렬화되며, 그 안의 예외는 JobError 로
    # 감싸져 500 이 된다 — "없는 주제/타입" 은 잡 실행 전에 읽기로 미리 검사해
    # 404 로 매핑한다(단일 사용자·직렬화라 TOCTOU 무해).
    def _resolve_type_kind(self, theme_id: str, name: str) -> str | None:
        for kind in ("node", "edge"):
            if self.themes.resolve_type(theme_id, kind, name) is not None:
                return kind
        return None

    def write_type_ratify(self, body):
        theme_id, name = _req(body, "theme_id"), _req(body, "name")
        if not self.themes.has_theme(theme_id):
            return 404, {"error": f"unknown theme_id: {theme_id}"}
        if self._resolve_type_kind(theme_id, name) is None:
            return 404, {"error": f"type not found: {name}"}
        res = self.jobs.run_sync("type_ratify", lambda: self.themes.ratify(theme_id, name))
        return 200, {"type": res}

    def write_type_reject(self, body):
        theme_id, name = _req(body, "theme_id"), _req(body, "name")
        if not self.themes.has_theme(theme_id):
            return 404, {"error": f"unknown theme_id: {theme_id}"}
        ok = self.jobs.run_sync("type_reject", lambda: self.themes.reject(theme_id, name))
        return 200, {"removed": bool(ok), "name": name}

    def write_type_rename(self, body):
        theme_id = _req(body, "theme_id")
        from_name, to_name = _req(body, "from_name"), _req(body, "to_name")
        if not self.themes.has_theme(theme_id):
            return 404, {"error": f"unknown theme_id: {theme_id}"}
        if self._resolve_type_kind(theme_id, from_name) is None:
            return 404, {"error": f"type not found: {from_name}"}
        res = self.jobs.run_sync("type_rename", lambda: self.themes.rename(
            theme_id, from_name, to_name))
        return 200, {"type": res}

    # ── 비동기 잡(ingest / backup) ────────────────────────────────────────
    def submit_ingest(self, engine, body):
        if engine == "inbox":
            # Phase E2 — Pull inbox: 디렉토리의 공유 팩 일괄 수입(긴 작업 → 202 잡).
            path, owner = _req(body, "path"), _req(body, "owner")
            job = self.jobs.submit("ingest:inbox",
                                   lambda: share.scan_inbox(path, owner=owner, store=self.store))
            return 202, job.to_dict()
        if engine == "pack":
            # share pack 들여오기(D19) — 수신자 owner 재스탬핑, 잡 큐 직렬화(R4).
            # 팩도 상한을 탄다: 새 주제의 주요 생성 경로라 빼두면 그대로 우회로가 된다.
            owner = _req(body, "owner")
            cap = _pop_max_domains(body)
            res = self.jobs.run_sync("ingest:pack",
                                     lambda: share.import_share_pack(
                                         body.get("pack") or {}, owner=owner, store=self.store,
                                         max_domains=cap))
            return 200, res
        if engine == "fragment":
            # MCP vinc_ingest(D11 §5) — 단일 write 표면 유지를 위해 잡 큐 직렬화(R4).
            # fragment MERGE 는 짧은 뮤테이션이라 동기(run_sync→200). themes 전달로
            # 개방층(F1) 어휘 편입 활성 — /ingest 와 동일 계약.
            cap = _pop_max_domains(body)
            res = self.jobs.run_sync("ingest:fragment",
                                     lambda: frag.merge_fragment(body, store=self.store,
                                                                 themes=self.themes,
                                                                 max_domains=cap,
                                                                 read_back=True))
            return 200, res
        fn = self.ingest.get(engine)
        if fn is None:
            return 400, {"error": f"unknown ingest engine: {engine}"}
        job = self.jobs.submit(f"ingest:{engine}", lambda: fn(self.store, body))
        return 202, job.to_dict()

    # ── 동기화 데이터플레인(S1) ───────────────────────────────────────────────
    # 이 라우트들은 **사이드카 내부 표면**이다(8787 은 Caddy 미노출, F4). 원격에서는
    # vinc_mcp/http_server 의 /sync/push|pull 이 인증(resolve_auth)을 거쳐 bridge 로
    # 여기에 프록시한다 — owner 는 항상 그쪽이 토큰에서 해석해 넘긴다.
    def read_sync_domains(self, p):
        """선택 가능 도메인 + 현재 선택. 오프라인 우선 UI 의 데이터원."""
        owner = _req(p, "owner")
        return self._read(lambda r: {
            "executed": True,
            "selected": sync.get_selected_domains(r),
            "domains": q.domains(owner=owner, store=r).get("rows", []),
        })

    def write_sync_domains(self, body):
        """선택 도메인 저장(정본은 사이드카 — CLI·재설치에도 일관)."""
        doms = body.get("domains")
        if not isinstance(doms, list):
            return 400, {"error": "missing field: domains (list)"}
        res = self.jobs.run_sync(
            "sync:domains", lambda: {"executed": True,
                                     "selected": sync.set_selected_domains(self.store, doms)})
        return 200, res

    def read_sync_pull_delta(self, p):
        """이 스토어의 delta 를 번들로 — 원격에선 pull, 로컬에선 push 소스.

        CRITICAL 회귀 수정(실측 확정, 2026-07-21): `sync.export_delta` 는 `domains`
        미지정 시 `sync.get_selected_domains(store)` 로 폴백한다 — 이건 **로컬
        디바이스의 Settings UI 도메인 체크박스** 선택값을 읽는, owner 로 구분되지
        않는 전역 메타키(`"selected_domains"`)다. 이 핸들러는 허브(mcp.vincs.app)의
        **공유 멀티테넌트 스토어**에서 호출되는데, 허브엔 그 키를 채우는 라우트가
        아예 없다(`/sync/domains` 는 로컬 사이드카 전용) — 그래서 매 pull 마다
        `get_selected_domains` 가 항상 빈 리스트를 돌려주고, `export_delta` 는
        "선택 도메인 없음 = 빈 번들"로 즉시 강등한다. 결과: **모든 회원의 pull 이
        100% 결정론적으로 빈 데이터** — "계정 키 넣고 동기화해도 안 불러와짐"의
        근본원인. 수정: 이 owner 가 실제로 가진 전 도메인을 명시로 넘겨 그 폴백을
        우회한다(허브는 "이 owner 의 전부를 서빙"이 맞는 의미 — 로컬쪽 도메인 선택은
        push 시 로컬 스토어에서 이미 적용됨, apply_delta 는 도메인 필터가 없다)."""
        owner = _req(p, "owner")
        if _flag(p.get("all")):
            # 모드 전환의 전량 받기(VIN-130). 아래 도메인 목록은 **주제가 빈 노드를 못
            # 잡는다**(`IN` 은 빈 값과 맞지 않는다) — 그대로 두면 계정에서 기기로 옮길 때
            # 주제 없는 노드가 조용히 남는다. 그래서 주제를 아예 안 가린다.
            return self._read(lambda r: sync.export_delta(
                owner=owner, since=_int(p.get("since"), 0), everything=True,
                cfg=self.cfg, store=r))
        return self._read(lambda r: sync.export_delta(
            owner=owner, since=_int(p.get("since"), 0),
            domains=[row["domain"] for row in q.domains(owner=owner, store=r).get("rows", [])
                     if row.get("domain")],
            cfg=self.cfg, store=r))

    def write_sync_apply_delta(self, body):
        """delta 번들 적용(쓰기 → 잡 큐 직렬화, R4)."""
        owner = _req(body, "owner")
        bundle = body.get("bundle") or {}
        res = self.jobs.run_sync("sync:apply", lambda: sync.apply_delta(
            owner=owner, bundle=bundle, cfg=self.cfg, store=self.store))
        return 200, res

    def read_sync_docs_export(self, p):
        """문서 정본 delta(D45 P3) — sync_seq > since 인 문서(복원 텍스트)+삭제 표식.
        노드 sync 와 별개 채널: 텍스트만 전송하고 embedding·청크는 수신측 재생성."""
        owner = _req(p, "owner")
        # `all` = 순번과 무관하게 전부(모드 전환의 전량 받기, VIN-130 · docsync 주석).
        return self._read(lambda r: docsync.export_docs(
            r, owner=owner, since=_int(p.get("since"), 0), everything=_flag(p.get("all"))))

    def write_sync_docs_apply(self, body):
        """문서 정본 delta 적용(D45 P3 — doc 단위 LWW, 로컬 재청킹). 쓰기 → 잡 직렬화.
        embedding 은 수신측에서 재생성하지 않는다(P3 시작본은 텍스트+FTS; 벡터는 후속
        재임베딩 잡이 채운다) — 정본 텍스트 정합이 우선."""
        owner = _req(body, "owner")
        payload = body.get("payload") or {}
        res = self.jobs.run_sync("sync:docs-apply", lambda: docsync.import_docs(
            self.store, owner=owner, payload=payload))
        return 200, res

    def write_sync_purge_owner(self, body):
        """owner 소유 전 노드 DETACH DELETE (계정 삭제 캐스케이드, §5.9).

        원격에서는 vinc_mcp/http_server 의 /internal/purge-owner 가 계정 서명
        어서션을 검증한 뒤 이 라우트로 프록시한다 — owner 는 그쪽이 서명된
        `sub` 에서 유도해 실어 보낸다(스코프 위조 불가). 비가역 쓰기 → 잡 큐
        직렬화(R4, 단일 writer)."""
        owner = _req(body, "owner")
        res = self.jobs.run_sync("sync:purge", lambda: q.purge_owner(
            owner, cfg=self.cfg, store=self.store))
        return 200, res

    def write_space_proxy(self, body):
        """스페이스 통과로 — 그래프 호출 하나를 계정으로 넘긴다(`api/spaceproxy.py`).

        `team` 이 있으면 그 팀 그래프, 없으면 키 주인의 개인 그래프다. 데스크톱 계정
        모드는 모든 화면 호출을 이 길로 보낸다(VIN-126).

        **로컬 스토어를 안 건드린다.** 계정 그래프는 여기 없다. 그래서 스토어가 안 열린
        상태에서도 답해야 하고(`_POST_WITHOUT_STORE`), 쓰기 신호도 안 낸다 — 이 호출로
        바뀌는 것은 계정 쪽 그래프라 로컬 동기화가 나를 것이 없다.

        키는 **바디로만** 온다(동기화와 같은 규칙, D36). 사이드카는 키를 저장하지 않는다.
        """
        st, out = spaceproxy.call(
            token=str(body.get("token") or ""),
            team=str(body.get("team") or ""),
            method=str(body.get("method") or "GET"),
            path=str(body.get("path") or ""),
            params=body.get("params") or {},
            body=body.get("body"),
            base=body.get("base"),
        )
        return st, out

    def submit_sync(self, body):
        """동기화 세션 트리거(S2) — pull → push 를 잡으로(202+job_id).

        **회원 키는 바디로만** 받는다(env·레포·디스크 반입 없음, D36). 원격 왕복이
        오래 걸릴 수 있어 submit(202) — 앱은 /jobs/{id} 로 폴링한다.
        동시 sync 는 sync_running 플래그로 거부(409) — 커서 경합 방지."""
        token = body.get("token")
        if not token:
            return 400, {"error": "missing field: token (member key)"}
        if self._sync_running:
            return 409, {"error": "a sync is already in progress"}
        owner = body.get("owner") or "user:local"   # 앱이 자기 로컬 owner 를 실어 보낸다
        remote = sync.RemoteHTTP(token, body.get("base"))

        def _job():
            self._sync_running = True
            try:
                return sync.run(self.store, self.cfg, owner=owner, remote=remote)
            finally:
                self._sync_running = False

        job = self.jobs.submit("sync", _job)
        return 202, job.to_dict()

    # ── 저장 위치와 모드 전환(VIN-130) ─────────────────────────────────────────
    # 데스크톱의 저장 설정은 `~/.vinc/config.json` 한 곳이다(`store.config`). 앱은 여기로
    # 읽고 쓰고, 폴더가 바뀌면 셸이 사이드카를 다시 띄운다(열린 스토어를 갈아 끼우는 일은
    # 이 프로세스가 하지 않는다: 단일 writer 를 중간에 바꾸면 도는 잡이 옛 파일에 쓴다).
    # 그래서 이 라우트들은 **스토어 없이도** 답한다. 폴더를 잘못 골라 스토어가 안 열렸을 때
    # 다시 고를 길이 여기뿐이다.

    #: `--data` 로 띄웠는가(`serve` 가 채운다). 그러면 설정의 폴더는 이 프로세스에 안 먹는다.
    data_pinned = False

    def _data_pinned(self) -> bool:
        return bool(self.data_pinned or os.environ.get("MEMORY_DATA_DIR"))

    def _config_view(self) -> dict:
        home = store_config.vinc_home()
        conf = store_config.read_local_config(home)
        cur = getattr(self.cfg, "data_dir", None)
        pinned = self._data_pinned()
        want = store_config.resolve_data_dir(create=False)
        mode = conf.get("mode")
        return {
            "executed": True,
            # 아직 안 정했으면 None 이다. 앱이 첫 기동에 정해 적는다(기존 설치의 데이터를
            # 보고). 여기서 "account" 로 채우면 앱은 "이미 정했다"로 읽고 그 판단을 건너뛴다.
            "mode": mode if mode in ("account", "local") else None,
            "data_path": conf.get("data_path") or None,
            "data_file": cur,
            "default_folder": str(home),
            "suggested_folder": localmode.suggested_folder(home),
            "pinned": pinned,
            # 설정이 가리키는 파일과 지금 연 파일이 다르다 = 셸이 사이드카를 다시 띄워야 한다.
            "restart_needed": bool(cur) and not pinned
                              and os.path.abspath(want) != os.path.abspath(cur),
            "store_open": self.store is not None,
        }

    def read_config(self, p):
        """저장 설정과 지금 연 스토어. 설정 화면의 데이터원."""
        return 200, self._config_view()

    def read_config_inspect(self, p):
        """폴더 하나를 받기 전에 살핀다(`api.localmode.inspect_folder`). 아무것도 안 쓴다."""
        return 200, {"executed": True, **localmode.inspect_folder(str(p.get("path") or ""))}

    def write_config(self, body):
        """저장 설정을 바꾼다: `mode`(account|local) · `data_path`(폴더, 빈 값 = 기본).

        폴더는 받기 전에 살핀다. 쓸 수 없으면 400, 동기화 폴더·네트워크 드라이브면
        `accept_risk` 없이 409 다(사람이 경고를 보고 한 번 더 골라야 받는다). 폴더는
        **만들기만 한다**: 안에 있던 것은 지우지도 덮지도 않는다. 이미 스토어(`db`)가
        있으면 다음 기동이 그것을 연다.

        설정만 쓴다. 새 폴더로 옮기는 것은 셸의 재기동이고(`restart_needed`), 데이터를
        나르는 것은 `/sync/pull-all`·`/sync/push-all` 이다."""
        updates: dict = {}
        info = None
        if "mode" in body:
            mode = body.get("mode")
            if mode not in ("account", "local"):
                return 400, {"error": "mode must be 'account' or 'local'"}
            updates["mode"] = mode
        if "data_path" in body:
            if self._data_pinned():
                return 409, {"error": "pinned",
                             "note": "this process was started with a fixed data path "
                                     "(--data or MEMORY_DATA_DIR)"}
            raw = body.get("data_path")
            if raw in (None, ""):
                updates["data_path"] = None
            else:
                info = localmode.inspect_folder(str(raw))
                if not info["ok"]:
                    return 400, {"error": info["problem"], "inspect": info}
                if info["risks"] and not _flag(body.get("accept_risk")):
                    return 409, {"error": "risky_folder", "inspect": info}
                try:
                    os.makedirs(info["path"], exist_ok=True)
                except OSError as exc:
                    return 400, {"error": "not_writable", "inspect": info,
                                 "note": f"{type(exc).__name__}: {exc}"}
                home = store_config.vinc_home()
                same = os.path.normcase(os.path.abspath(info["path"])) \
                    == os.path.normcase(os.path.abspath(str(home)))
                # 기본 폴더를 고른 것은 설정이 없는 것과 같다. 적어 두면 홈을 옮겼을 때
                # 옛 경로를 가리키는 설정만 남는다.
                updates["data_path"] = None if same else info["path"]
        if not updates:
            return 400, {"error": "nothing to change (mode, data_path)"}
        store_config.write_local_config(updates)
        view = self._config_view()
        if info is not None:
            view["inspect"] = info
        return 200, view

    #: `serve` 가 채우는 정지 스위치. 테스트·임베드처럼 `serve` 밖에서 만든 앱은 None 이다.
    _request_stop = None

    def write_shutdown(self, body):
        """스스로 닫는다(VIN-130). 셸이 저장 폴더를 바꾼 뒤 사이드카를 다시 띄울 때 먼저
        부른다. 죽이지 않고 부탁하는 이유: 이 길은 `close()` 를 지나 스토어를 닫는다
        (체크포인트). 강제로 끊으면 다음 기동이 WAL 재생에 기대는데, 허브를 세 번
        세운 고장이 그 재생이었다.

        응답을 먼저 보내고 잠깐 뒤에 멈춘다. 스토어 없이도 받는다: 폴더를 잘못 골라
        스토어가 안 열린 사이드카야말로 다시 띄워야 한다."""
        stop = self._request_stop
        if stop is None:
            return 409, {"error": "this process was not started by serve"}
        threading.Timer(0.2, stop).start()
        return 202, {"executed": True, "note": "stopping"}

    def _submit_move(self, name: str, fn):
        """모드 전환의 데이터 이동을 잡으로. 동기화 세션과 같은 깃발을 쓴다(커서는 안
        건드리지만 같은 원격에 같은 소유자로 쓰므로 겹치면 안 된다). 깃발은 **접수 때**
        세운다: 잡 안에서 세우면 연달아 두 번 누른 요청이 둘 다 통과한다."""
        if self._sync_running:
            return 409, {"error": "a sync or move is already in progress"}
        self._sync_running = True

        def _job():
            try:
                return fn()
            finally:
                self._sync_running = False

        try:
            job = self.jobs.submit(name, _job)
        except Exception:
            self._sync_running = False
            raise
        return 202, job.to_dict()

    def submit_sync_push_all(self, body):
        """로컬 → 계정: 이 기기의 모든 주제를 계정으로(`sync.push_everything`). 202+job.

        이 스토어에는 아무것도 안 쓴다. 결과의 `missing` 이 0 이 아니면 앱은 모드를
        바꾸지 않는다."""
        token = body.get("token")
        if not token:
            return 400, {"error": "missing field: token (member key)"}
        owners = body.get("owners") or ["tenant:local", "user:local"]
        if not isinstance(owners, list) or not all(isinstance(o, str) and o for o in owners):
            return 400, {"error": "owners must be a list of owner ids"}
        remote = sync.RemoteHTTP(token, body.get("base"))
        return self._submit_move("sync:push-all", lambda: sync.push_everything(
            self.store, self.cfg, owners=owners, remote=remote))

    def submit_sync_pull_all(self, body):
        """계정 → 로컬: 계정 그래프 전부를 이 스토어로(`sync.pull_everything`). 202+job."""
        token = body.get("token")
        if not token:
            return 400, {"error": "missing field: token (member key)"}
        owner = body.get("owner") or "tenant:local"
        remote = sync.RemoteHTTP(token, body.get("base"))
        return self._submit_move("sync:pull-all", lambda: sync.pull_everything(
            self.store, self.cfg, owner=owner, remote=remote))

    def submit_backup(self, body):
        out = body.get("out")
        if not out:
            return 400, {"error": "missing field: out"}
        job = self.jobs.submit("backup", lambda: make_backup(
            self.store, out, packs_dir=self.packs_dir, profile_path=self.profile_path))
        return 202, job.to_dict()

    def get_job(self, job_id):
        job = self.jobs.get(job_id)
        if job is None:
            return 404, {"error": f"no such job: {job_id}"}
        return 200, job.to_dict()


# ── 파라미터 헬퍼 ────────────────────────────────────────────────────────────

class _Missing(KeyError):
    """필수 필드 누락(핸들러가 400 으로 매핑)."""


def _req(p: dict, key: str):
    val = p.get(key)
    if val in (None, ""):
        raise _Missing(key)
    return val


def _accepts_kw(fn, name: str) -> bool:
    """`fn` 이 키워드 `name` 을 받는가. 시그니처를 못 읽으면 **안 받는 것으로** 본다.

    스토어 인터페이스는 여러 구현이 있고(임베디드·테스트 더블·향후 백엔드) 새 키워드를
    강제하면 그것들이 통째로 깨진다. 확실할 때만 넘긴다."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    if name in params:
        return True
    return any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


def _domains(val) -> list[str] | None:
    """``domains=a,b`` 를 목록으로. 주제 이름에 쉼표가 없다는 것이 전제이고, 그것은
    이 코퍼스의 관례다(소문자 영문 + 슬래시 계층 — GRAPH.md). 빈 값은 ``None`` 이라
    호출측이 "주제 지정 없음"과 "빈 목록"을 구분하지 않아도 된다."""
    if not val:
        return None
    out = [d.strip() for d in str(val).split(",")]
    return [d for d in out if d] or None


def _float(val, default: float) -> float:
    """숫자 아닌 값은 조용히 기본으로 — 배치 인자는 화면 손잡이라, 잘못된 값에
    500 을 내는 것보다 여태의 그림을 내는 편이 낫다(범위는 query 가 잡는다)."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _int(val, default: int) -> int:
    try:
        return int(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def embed_apply_paused() -> bool:
    """`VINC_EMBED_APPLY_PAUSED` 가 켜졌는가(on/1/true/yes). 매번 env 를 읽는다."""
    return _flag(os.environ.get("VINC_EMBED_APPLY_PAUSED"))


def _heal_vector_index(store, dim: int) -> None:
    """기동 때 벡터 색인을 세우고, 잔해가 막으면 `ChunkVec` 을 다시 떠서 고친다.

    **HTTP 가 뜨기 전에** 돈다 — 표를 버리고 만드는 DDL 이라 읽기와 겹치면 안 된다.
    허브 사본(벡터 11,905개) 1코어 실측: 실패하는 빌드 시도를 포함해 64초라 엔트리포인트의
    기동 대기(300초) 안이다.
    실패는 기동을 죽이지 않는다: 키워드 검색은 살아 있고, 사유는 로그와 `/health` 에 남는다."""
    heal = getattr(store, "heal_vector_index", None)
    if heal is None:
        return
    try:
        res = heal(dim=dim)
    except Exception as exc:     # noqa: BLE001 — 기동 경로, 죽이지 않는다
        print(f"vinc: ⚠ vector index heal failed — {type(exc).__name__}: {exc}",
              file=sys.stderr, flush=True)
        return
    if res.get("state") in ("built", "repaired"):
        print(f"vinc: vector index {res['state']} at boot "
              f"({res.get('rows')} vectors, {res.get('seconds')}s)", file=sys.stderr, flush=True)


def _flag(val) -> bool:
    """쿼리스트링·JSON 양쪽에서 오는 불리언. HTTP 로 오면 문자열 `"true"` 이고
    MCP/CLI 로 오면 진짜 bool 이라 둘 다 받아야 한다 — `bool("false")` 가 True 인
    것이 이 자리의 함정이다(F3 의 debug 옵트인이 항상 켜져 버린다)."""
    if isinstance(val, bool):
        return val
    return str(val or "").strip().lower() in ("1", "true", "yes", "on")


def _pop_max_domains(body) -> int | None:
    """요청 본문에서 주제 상한을 꺼낸다(있으면 제거).

    등급 → 숫자 변환은 **허브가** 한다(AP-1 §3.4). 사이드카는 정책을 모르는 채
    숫자만 받는다 — 여기서 등급을 해석하면 한도 설정이 두 곳에 살게 되고 반드시
    어긋난다. 키가 없으면 None = 무제한이라, 로컬·직접 호출·동기화는 자동 면제다.
    """
    if not isinstance(body, dict):
        return None
    ent = body.pop("_entitlements", None)
    if not isinstance(ent, dict):
        return None
    v = ent.get("max_domains")
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


def _csv(val):
    if not val:
        return None
    if isinstance(val, list):
        return val
    return [s.strip() for s in val.split(",") if s.strip()]


# ── HTTP 핸들러 ──────────────────────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):   # 서버 로그 소음 억제(사이드카 — 셸이 제어)
        pass

    @property
    def app(self) -> VincApp:
        return self.server.app   # type: ignore[attr-defined]

    def _send(self, code: int, obj: dict):
        body = (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # 셸 웹뷰(tauri://localhost · http://tauri.localhost)는 사이드카(127.0.0.1)와
        # 교차출처 → CORS 없으면 fetch 가 브라우저에 차단된다("Failed to fetch"). 루프백
        # 바인딩 + Bearer 토큰 인증이라 origin 은 * 로 허용해도 무단 접근은 토큰에서 막힌다.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_OPTIONS(self):
        # CORS preflight — Authorization·Content-Type 헤더 때문에 브라우저가 실제 요청
        # 전에 OPTIONS 를 먼저 보낸다. 허용 헤더/메서드를 응답해야 본 요청이 진행된다.
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "authorization, content-type")
        # 사설망 승인(계정 연결 콜백 전용). 공개 오리진(vincs.io)에서 루프백으로 가는
        # 요청은 CORS 를 통과해도 브라우저가 **한 겹 더** 막는다 — preflight 에
        # `Access-Control-Request-Private-Network: true` 를 실어 보내고 같은 이름의
        # 허용 헤더를 요구한다. 그 헤더가 없어서 `/connect/callback` 이 브라우저에서
        # 도달 불가였고, 연결 플로우가 매번 수동 키 폴백으로 떨어졌다.
        #
        # `*` 로 열지 않고 콜백 오리진에만 내주는 이유: 이 서버에는 무인증 라우트가
        # 둘 있다(`/health` · `/connect/callback`). 아무 사이트에나 사설망을 허용하면
        # 그 둘이 웹 전체에 노출된다. 콜백은 state 논스로, 나머지는 Bearer 로 막히지만
        # 승인 자체를 좁히는 편이 싸다.
        if self.headers.get("Access-Control-Request-Private-Network") == "true" \
                and self.headers.get("Origin") in connect_origins():
            self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Access-Control-Max-Age", "86400")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _dispatch(self, method: str):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        # /health — 무인증 준비성 프로브.
        if method == "GET" and path == "/health":
            # 기동은 했지만 스토어가 성치 않은 상태(프로비저닝 실패·드라이버 부재)를
            # 숨기지 않는다 — "ok" 만 보고 정상이라 믿으면 뒤따르는 전 조회가 원인
            # 불명으로 깨진다(실패 신호 설계: 조용한 성공 금지).
            body = {"status": "ok"}
            if getattr(self.app, "provision_error", None):
                body = {"status": "degraded", "provision_error": self.app.provision_error}
            elif self.app.store is None:
                # ⚠ **사유를 여기까지 들고 온다** — "no reachable store" 한 줄이면 앱
                # 화면이 빈 그래프와 구분되지 않는다. 조립은 `store_unavailable()` 한
                # 벌이라 조회 503 과 문장이 갈라지지 않는다:
                # [[record:a-failure-reason-nothing-reads-is-not-an-error-message]].
                body = {"status": "degraded", **store_unavailable()}
                body.pop("note", None)   # note 는 조회 봉투 전용 — 헬스 계약 불변
            else:
                # 임베딩 백로그 — 정체가 밖에서 보여야 한다(App.embed_health 주석).
                # 진단이 헬스를 죽이지 않게 통째로 감싼다: 여기서 예외가 나가면
                # 엔트리포인트의 기동 대기가 실패하고 워치독이 사이드카를 재기동시킨다.
                # `?live=1` 은 **스토어를 안 읽는** 생존 확인이다. 엔트리포인트 워치독이
                # 5초마다 부르는데, 그때마다 리더를 열고 백로그를 세던 것이 허브를 하루
                # 종일 활성 요금 구간에 붙잡아 둔 원인 후보였다. 상태 판정(ok·degraded)과
                # 스냅샷 상태는 메모리 값이라 그대로 싣는다.
                # 근거: [[vinc.ops.container-apps-cost-rise-2026-09-25]] §4
                if not _flag(urllib.parse.parse_qs(parsed.query).get("live", [""])[0]):
                    try:
                        body["embed"] = self.app.embed_health()
                    except Exception as exc:   # noqa: BLE001
                        body["embed"] = {"error": f"{type(exc).__name__}: {exc}"}
                # 영속 스냅샷 상태 — 꺼져 있으면 'off'. 이게 조용히 실패하면
                # "돌고 있다고 믿는 백업이 없는" 최악의 형태가 된다(이 리포가
                # 임베딩 워커에서 38시간 겪은 그 모양).
                # 상태 문자열만으로는 "언젠가 됐다"와 "방금 됐다"가 구분되지 않는다.
                # `age_s`(마지막 **성공** 이후 경과)가 곧 지금 잃을 수 있는 양의
                # 하한이고, null 이면 이 프로세스가 사는 동안 한 번도 성공한 적이
                # 없다는 뜻이다 — 그게 가장 위험한 값이다(SnapshotPolicy.detail 주석).
                snaps = getattr(self.app, "_snapshots", None)
                if snaps is not None:
                    det = getattr(snaps, "detail", None)
                    body["snapshot"] = (det() if callable(det)
                                        else getattr(snaps, "last_state", "unknown"))
            if embed_apply_paused():
                body["embed_apply_paused"] = True
            # 벡터 색인이 잔해로 막혔다 — 의미 팔이 꺼져 있고 임베딩 적용이 거절된다.
            # 재기동이 고친다(`_heal_vector_index`). 정상이면 키가 없다.
            if getattr(self.app.store, "vector_index_remnant", False):
                body["vector_index"] = "remnant"
            return self._send(200, body)

        # /connect/callback — 무인증 예외(디바이스 연결: 브라우저의 루프백 전달,
        # RFC 8252 변형). 인증은 start 가 발급한 state 논스(단일사용·TTL)가 대신하고,
        # Origin 화이트리스트가 심층방어로 얹힌다(핸들러 주석 참조).
        if method == "POST" and path == "/connect/callback":
            try:
                code, obj = self.app.connect_callback(self._body(), self.headers.get("Origin"))
            except json.JSONDecodeError as exc:
                code, obj = 400, {"error": f"invalid JSON body: {exc}"}
            return self._send(code, obj)

        # 인증(F4): Bearer 세션 토큰 필수.
        if not self.app.auth.check_header(self.headers.get("Authorization")):
            return self._send(401, {"error": "unauthorized"})

        try:
            if method == "GET":
                params = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
                code, obj = self._route_get(path, params)
            else:
                body = self._body()
                # 소유자는 **라우팅 전에** 읽는다. 핸들러가 바디를 고쳐 쓰는 일이 있다.
                owner = body.get("owner") if isinstance(body, dict) else None
                code, obj = self._route_post(path, body)
                self.app.note_write(path, owner, code, obj)
        except _Missing as exc:
            code, obj = 400, {"error": f"missing field: {exc.args[0]}"}
        except json.JSONDecodeError as exc:
            code, obj = 400, {"error": f"invalid JSON body: {exc}"}
        except JobError as exc:
            code, obj = 500, {"error": str(exc)}
        except Exception as exc:   # noqa: BLE001 — 요청 격리(한 요청 오류가 서버를 안 죽임)
            code, obj = 500, {"error": f"{type(exc).__name__}: {exc}"}
        self._send(code, obj)

    # ── 라우트 표(CLI 1:1) ────────────────────────────────────────────────
    def _route_get(self, path: str, p: dict):
        app = self.app
        table = {
            "/recall": app.read_recall, "/domains": app.read_domains,
            "/view": app.read_view, "/ledger": app.read_ledger, "/share": app.read_share,
            "/coupling": app.read_coupling, "/promote-candidates": app.read_promote,
            "/suggest-ties": app.read_suggest, "/diagnose": app.read_diagnose,
            "/merge-candidates": app.read_merge_candidates,
            "/merge-rejections": app.read_merge_rejections,   # F10-b 기각 목록
            "/neighbors": app.read_neighbors, "/path": app.read_path,
            "/learner": app.read_learner, "/graph": app.read_graph,
            "/search": app.read_search, "/decide": app.read_decide,
            "/themes": app.read_themes, "/types": app.read_types,
            # 문서 정본(D45 P4) — get(복원+검증) · list. + 노드 클릭 상세(D45-V).
            "/doc": app.read_doc, "/docs": app.read_docs,
            # 관례(GRAPH.md) — 어시스턴트가 쓰기 전에 읽는 채널([[vinc.canon.graph-conventions]]).
            "/conventions": app.read_conventions,
            "/nav-tree": app.read_nav_tree,   # 좌측 문서 트리(D69)
            # 변경 표식(VIN-129) — 열린 화면이 몇 초마다 묻는다. 스토어를 안 읽는다.
            "/changes": app.read_changes,
            "/doc/highlights": app.read_doc_highlights,   # Reader 밑줄(D45-V2 2단계)
            "/doc/context": app.read_doc_context,   # 리더 머리(개념 칩·목차·청크 오프셋)
            "/node-context": app.read_node_context,
            "/brief": app.read_brief,   # W1 — 질문 하나로 답변 재료 조인
            "/edge-context": app.read_edge_context,   # 흐름 선 클릭(D45-V 1단계)
            "/ledger-drilldown": app.read_ledger_drilldown,   # 합계 구간 클릭(D45-V2 3단계)
            "/docs-in-range": app.read_docs_in_range,   # 타임라인 기간 브러시(D45-V2 4단계)
            # 동기화(S1) — 선택 도메인 조회 · delta 번들 export.
            "/sync/domains": app.read_sync_domains,
            "/sync/pull-delta": app.read_sync_pull_delta,
            "/sync/docs-export": app.read_sync_docs_export,   # 문서 정본 delta(D45 P3)
            # 저장 설정(VIN-130) — 스토어 없이 답한다.
            "/config": app.read_config, "/config/inspect": app.read_config_inspect,
            "/embed/pending": app.read_embed_pending,   # 자체호스팅 임베딩 워커(B, 내부)
            "/owner-stats": app.read_owner_stats,   # 운영자 사용량 덤프(W1c, 허브 내부 전용)
            # 계정 연결(디바이스 플로우) — 메일박스 1회 수령(인증).
            "/connect/status": app.read_connect_status,
        }
        if path in table:
            return table[path](p)
        if path.startswith("/plan/"):
            return app.read_plan(path[len("/plan/"):], p)
        if path.startswith("/jobs/"):
            return app.get_job(path[len("/jobs/"):])
        return 404, {"error": f"no such route: GET {path}"}

    #: 스토어 없이도 **옳게** 도는 POST. 어휘(themes·types)는 그래프 밖 JSON 파일이고
    #: (`ThemeStore`, `<data_dir>.themes.json`), 계정 연결 시작은 인메모리 메일박스다.
    #: 그것들은 그래프가 죽어도 답하는 게 맞다 — GET 쪽 `READ_ROUTES` 주석과 같은 갈래다.
    #: ⚠ 새 POST 라우트를 더할 때 여기 넣을지 판단할 것. 기본값은 **넣지 않는다**(가드를
    #: 받는다) — 빠뜨려도 조용히 새지 않고 503 으로 막히므로 안전측이다.
    _POST_WITHOUT_STORE = frozenset({
        "/themes", "/themes/activate",
        "/types/ratify", "/types/reject", "/types/rename",
        "/connect/start",
        # 팀 그래프는 로컬에 없다. 스토어가 안 열렸다고 막으면, 로컬이 깨진 기기에서
        # **팀 스페이스까지 같이 죽는다** — 그쪽은 이 스토어와 아무 상관이 없다.
        "/space/proxy",
        # 저장 설정은 그래프 밖 파일이다. 폴더를 잘못 골라 스토어가 안 열렸을 때 다시
        # 고를 길이 이것뿐이다(VIN-130).
        "/config",
        # 안 열린 사이드카도 닫고 다시 띄울 수 있어야 한다(VIN-130).
        "/shutdown",
    })

    def _route_post(self, path: str, body: dict):
        """POST 디스패치. **스토어가 안 열렸으면 여기서 503 이다.**

        읽기 경로는 2026-08-16 에 `VincApp._read` 한 곳으로 이 거절을 모았는데 쓰기
        경로에는 안 붙었다. 그래서 `store=None` 인 채로 핸들러가 그대로 불렸고,
        `sync.run(self.store, ...)` 이 `'NoneType' object has no attribute 'run'` 로
        터졌다. 2026-08-30 에 데스크탑에서 실제로 그랬다: WAL 이 깨져 스토어가 안 열린
        상태였고 `/health` 는 **같은 순간 진짜 사유를 알고 있었는데**("Checksum
        verification failed, the WAL file is corrupted.") 화면에 닿은 것은 파이썬
        내부 사정뿐이라 사용자는 원인도 처방도 못 봤다.

        봉투는 새로 만들지 않는다 — `store_unavailable()` 한 벌을 `_read`·`/health` 와
        같이 쓴다. 사유를 말하는 곳이 하나여야 셋이 같은 말을 한다."""
        app = self.app
        table = {
            "/pin": app.write_pin, "/tag": app.write_tag, "/touch": app.write_touch,
            "/activate": app.write_activate, "/backup": app.submit_backup,
            "/ingest": app.write_ingest,
            "/delete": app.write_delete, "/delete-edge": app.write_delete_edge,
            "/merge-nodes": app.write_merge,
            # F10-b 병합 후보 기각(가역) — 비가역 병합의 안전한 반대편.
            # TODO(vinc_mcp/tools.py): 이 경로를 부르는 MCP 도구가 아직 없다.
            #   `vinc_merge_reject {a, b, note?, undo?}` → POST /merge-reject 와
            #   `vinc_merge_candidates` 에 `include_structural: boolean` 을 추가해야
            #   호스트가 기각하고 되열 수 있다(스키마 변경이라 이 작업 범위 밖).
            "/merge-reject": app.write_merge_reject,
            # 문서 정본(D45 P4) — put(적재/편집) · delete. 단일 writer 직렬화.
            "/doc/put": app.write_doc_put, "/doc/delete": app.write_doc_delete,
            # 관례 재생성 — 생성 구역만 스토어 값으로 갈아 끼우고 저장한다.
            "/conventions/build": app.write_conventions_build,
            "/doc/put-file": app.write_doc_put_file,   # 파일 바이트 입구(텍스트·PDF)
            "/doc/extract": app.write_doc_extract,   # 정본→노드 파생(D45-V, 확인 흐름)
            "/doc/reembed": app.write_doc_reembed,   # 편집 후 벡터 재임베딩(D45-E 후속3)
            "/evidence/backfill": app.write_evidence_backfill,   # 관계 근거 저작(U2)
            "/embed/apply": app.write_embed_apply,   # 자체호스팅 임베딩 워커(B, 내부)
            # 노드 미러 전량 재구축 — 배포 경계에서 엔트리포인트가 한 번 부른다.
            "/node-mirror/rebuild": app.write_node_mirror_rebuild,
            "/domain/rename": app.write_domain_rename,   # 도메인 일괄 변경(S2)
            "/themes": app.write_theme_create, "/themes/activate": app.write_theme_activate,
            "/types/ratify": app.write_type_ratify, "/types/reject": app.write_type_reject,
            "/types/rename": app.write_type_rename,
            # 동기화 데이터플레인(S1) — 사이드카 내부 표면(8787 은 공개 미노출, F4).
            "/sync/domains": app.write_sync_domains,
            "/sync/apply-delta": app.write_sync_apply_delta,
            "/sync/docs-apply": app.write_sync_docs_apply,    # 문서 정본 delta 적용(D45 P3)
            # 계정 삭제 캐스케이드(§5.9) — owner 전 노드 purge. 원격에선 MCP
            # /internal/purge-owner 가 계정 서명 검증 후 프록시(owner 위조 불가).
            "/sync/purge-owner": app.write_sync_purge_owner,
            # 동기화 세션 트리거(S2) — pull→push 오케스트레이션(202+job_id).
            "/sync": app.submit_sync,
            # 모드 전환의 데이터 이동(VIN-130) — 전량, 한 번, 202+job_id.
            "/sync/push-all": app.submit_sync_push_all,
            "/sync/pull-all": app.submit_sync_pull_all,
            "/config": app.write_config,   # 저장 설정(VIN-130)
            "/shutdown": app.write_shutdown,   # 셸의 재기동 전 정상 종료(VIN-130)
            # 계정 연결(디바이스 플로우) — state 발급(인증). callback 은 _dispatch 의
            # 무인증 예외 경로(여기 표에 없음 — 표는 인증 이후에만 닿는다).
            "/connect/start": app.write_connect_start,
            # 팀 스페이스 통과로 — 로컬 스토어가 아니라 계정으로 나간다.
            "/space/proxy": app.write_space_proxy,
        }
        if path in table:
            if app.store is None and path not in self._POST_WITHOUT_STORE:
                return 503, {"executed": False, **store_unavailable()}
            return table[path](body)
        if path.startswith("/ingest/"):
            if app.store is None:
                return 503, {"executed": False, **store_unavailable()}
            return app.submit_ingest(path[len("/ingest/"):], body)
        return 404, {"error": f"no such route: POST {path}"}


# ── 서버 구성 · 수명주기(E6) ─────────────────────────────────────────────────

def make_server(app: VincApp, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    """루프백 바인딩 threaded 서버. port=0 → 임의 포트(반환된 server_address 로 확인)."""
    httpd = ThreadingHTTPServer((host, port), _Handler)
    httpd.app = app                 # 핸들러가 self.server.app 로 접근
    httpd.daemon_threads = True
    return httpd


def _pid_alive(pid: int) -> bool:
    """부모 PID 생존 확인 — **조회 전용, 대상을 죽이지 않는다.**

    구현은 `api/procutil.py` 한 벌뿐이다. 여기 사본을 두지 않는 이유는 그 파일에
    적어 뒀다 — 사본이 둘이던 시절 브리지 쪽만 `os.kill` 을 쓰다가 Windows 에서
    앱을 죽였다(2026-08-18).
    """
    return pid_alive(pid)


def _heartbeat(parent_pid: int, stop: threading.Event, interval: float = 1.0):
    """부모(셸) 프로세스 생존 감시. 사망 시 stop 을 세워 clean shutdown 유발(E6)."""
    while not stop.wait(interval):
        if not _pid_alive(parent_pid):
            stop.set()
            return


def _gc_tombstones_loop(app: VincApp, stop: threading.Event, interval: float = 86400.0):
    """tombstone 청소 루프 — 기동 직후 1회 + interval 주기. 안전 지평(90일)은
    `sync.gc_tombstones` 가 정한다([[vinc.spec.dataplane]] GC 계약). 쓰기는 반드시
    잡 큐를 지난다(R4 — 단일 워커가 곧 쓰기 직렬화)."""
    while True:
        try:
            n = app.jobs.run_sync("gc.tombstones",
                                  lambda: {"removed": sync.gc_tombstones(app.store)})
            print(f"vinc: gc tombstones — removed {n.get('removed', 0)}",
                  file=sys.stderr, flush=True)
        except Exception as exc:
            print(f"vinc: gc tombstones failed — {exc}",
                  file=sys.stderr, flush=True)
        if stop.wait(interval):
            return


def serve(cfg=None, *, host: str = "127.0.0.1", port: int = 0, parent_pid: int | None = None,
          token: str | None = None, packs_dir=None, profile_path=None,
          print_info: bool = True, data_pinned: bool = False) -> dict:
    """사이드카를 기동해 종료 신호까지 서비스한다(블로킹). 기동 정보(host/port/token/
    driver)를 stdout 에 1줄 JSON 으로 내보낸다. SIGTERM/SIGINT 또는 부모 사망 시 정리.

    **토큰은 `--token` 으로 받았으면 가린다.** 그 경우 호출자가 이미 값을 알고 있어
    되돌려줄 이유가 없고, 서버 환경에서는 이 stdout 이 로그 수집기(Azure Log
    Analytics)로 그대로 들어가 **시크릿이 조회 가능한 로그에 남는다**(실측). 반대로
    토큰을 사이드카가 **생성**한 경우는 이 줄이 유일한 전달 경로일 수 있으므로 그대로
    싣는다 — 구버전 소비자까지 안전한 규칙이다.
    (현행 소비자 확인: Tauri 셸은 토큰을 자기가 만들어 `--token` 으로 넘기고
    `sidecar_conn` 으로 반환하므로 배너를 파싱하지 않고, `vinc_mcp/bridge.py` 는
    `stdout=DEVNULL` 로 아예 버린다.)"""
    app = VincApp.open(cfg, auth=SessionAuth(token), packs_dir=packs_dir,
                       profile_path=profile_path)
    app.data_pinned = data_pinned
    httpd = make_server(app, host, port)
    bound_host, bound_port = httpd.server_address[:2]
    shown = f"<supplied:{len(app.auth.token)}ch>" if token else app.auth.token
    # **스냅샷 주기가 곧 최대 손실 창인데 여태 어디에도 안 찍혔다.** 그래서 설정을
    # 바꿔도 그것이 먹었는지 로그로 확인할 길이 없었다(2026-08-19 실측,
    # [[vinc.ops.deployment]]). 경로와 주기를 기동 배너에 싣는다 — 값은 비밀이 아니다.
    info = {"host": bound_host, "port": bound_port, "token": shown,
            "driver": getattr(app.store, "driver_name", None),
            "snapshot": resolve_snapshot_path(), "snapshot_every": resolve_snapshot_every()}
    if print_info:
        sys.stdout.write(json.dumps(info, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    stop = threading.Event()
    app._request_stop = stop.set
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    if parent_pid:
        threading.Thread(target=_heartbeat, args=(parent_pid, stop), daemon=True).start()
    threading.Thread(target=_gc_tombstones_loop, args=(app, stop), daemon=True).start()

    # 쓰기 자격은 HTTP 를 열기 **직전에** 가져온다. 이보다 늦으면 ACA 가 이 리비전을
    # 준비됐다고 보고 구 리비전을 내리는 쪽이 먼저 올 수 있다(persistence.claim_writer).
    writer = claim_writer()
    if writer != "off":
        print(f"vinc: store writer — {writer}", file=sys.stderr, flush=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        stop.wait()
    finally:
        httpd.shutdown()
        app.close()
    return info
