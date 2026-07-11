# -*- coding: utf-8 -*-
"""사이드카 HTTP 서버 — CLI 데이터 플레인 위 얇은 루프백 레이어(설계
[[weg.c.memory-standalone]] §11 P2·§3-1).

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

import json
import os
import signal
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from store.config import load_config
from store.backend import open_store

from . import query as q
from . import preference as pref
from . import activate as act
from . import fragment as frag
from . import share
from .auth import SessionAuth
from .backup import make_backup
from .jobs import JobQueue, JobError


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


# ── 앱(라우트 핸들러의 상태 컨테이너) ─────────────────────────────────────────

class VincApp:
    """사이드카 상태: 설정 · 공유 스토어(단일 Database, writer) · 세션 인증 · 잡 큐 ·
    ingest 레지스트리 · 백업 소스 경로. 라우트 메서드는 (status, dict) 를 돌려준다."""

    def __init__(self, cfg=None, *, store=None, auth=None, jobs=None,
                 packs_dir=None, profile_path=None, ingest=None, themes=None):
        self.cfg = cfg or load_config()
        self.store = store                    # 공유 writer 백엔드(단일 Database) 또는 None
        self.auth = auth or SessionAuth()
        self.jobs = jobs or JobQueue()
        self.packs_dir = packs_dir
        self.profile_path = profile_path
        self.ingest = ingest if ingest is not None else dict(DEFAULT_INGEST)
        # 주제·타입 어휘 스토어(그래프 밖 메타 — <data_dir>.themes.json, DB 파일 형제).
        # D11-R2. 그래프(store)와 분리 — 어휘는 사용자 저작, 그래프는 인스턴스.
        from store.theme_store import ThemeStore
        self.themes = themes if themes is not None else ThemeStore(self.cfg.data_dir)
        self._conn_lock = threading.Lock()    # reader Connection 파생 가드

    @classmethod
    def open(cls, cfg=None, **kw) -> "VincApp":
        cfg = cfg or load_config()
        return cls(cfg, store=open_store(cfg), **kw)   # 드라이버 미설치 시 store=None

    # reader 뷰: 공유 Database 에서 별도 Connection(R4). 없으면 None(각 q 함수가
    # 자체 open→None→강등). 요청 단위로 생성·close.
    def _reader(self):
        if self.store is None:
            return None
        with self._conn_lock:
            return self.store.reader_view()

    def _read(self, fn):
        """읽기 경로 공통: reader 뷰를 열어 fn(reader)->dict 실행 후 닫는다."""
        r = self._reader()
        try:
            return 200, fn(r)
        finally:
            if r is not None:
                r.close()

    def close(self):
        """clean shutdown(E6): 워커 드레인 → 공유 스토어 close."""
        self.jobs.shutdown(drain=True)
        if self.store is not None:
            self.store.close()
            self.store = None

    # ── 읽기 라우트(CLI 1:1) ──────────────────────────────────────────────
    def read_recall(self, p) -> tuple[int, dict]:
        return self._read(lambda r: q.recall(
            p.get("query", ""), owner=p.get("owner"), domain=p.get("domain"),
            tenant=p.get("tenant"), workspace=p.get("workspace"),
            rank=p.get("rank", "recency"), tags=_csv(p.get("tags")),
            limit=_int(p.get("limit"), 10), store=r))

    def read_domains(self, p):
        return self._read(lambda r: q.domains(owner=p.get("owner"), store=r))

    def read_learner(self, p):
        return self._read(lambda r: q.learner_model(owner=_req(p, "owner"), store=r))

    def read_graph(self, p):
        # seed 지정 시 seed+depth ego 서브그래프(Atlas L2 = MCP vinc_graph 동일 경로).
        # layout = D34 프리셋(shell·radial·circle·tree·organic — 미지 값 shell 강등).
        return self._read(lambda r: q.graph(owner=_req(p, "owner"),
                                            domain=p.get("domain"), seed=p.get("seed"),
                                            depth=_int(p.get("depth"), 2),
                                            layout=p.get("layout") or "shell", store=r))

    def read_atlas(self, p):
        # Atlas L0(D16) — 도메인 분포 + 결합 행렬.
        return self._read(lambda r: q.atlas_l0(owner=_req(p, "owner"), store=r))

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
        return self._read(lambda r: q.search(_req(p, "query"),
                                             top_k=_int(p.get("top_k"), 5),
                                             filters=filters or None, store=r))

    def read_decide(self, p):
        return self._read(lambda r: q.decide(_req(p, "topic"),
                                             depth=_int(p.get("depth"), 2), store=r))

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
        ep = _req(body, "episode")
        res = self.jobs.run_sync("pin", lambda: pref.pin(
            ep, pinned=not bool(body.get("off")), store=self.store))
        return 200, res

    def write_tag(self, body):
        ep = _req(body, "episode")
        tags = body.get("tags") or []
        if isinstance(tags, str):
            tags = _csv(tags) or []
        res = self.jobs.run_sync("tag", lambda: pref.tag(ep, list(tags), store=self.store))
        return 200, res

    def write_touch(self, body):
        ep = _req(body, "episode")
        res = self.jobs.run_sync("touch", lambda: pref.touch(
            ep, now=body.get("now"), store=self.store))
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
        # 적재 단일화(발주자 결정 2026-07-11) — `/ingest` 도 레거시 fragment 경로
        # (Record/props, D11 §5, api/fragment.merge_fragment)로 수렴한다. 테마 적재
        # (D11-R2 Entity/TYPED·engines.fragment_ingest)는 **휴면**: 스키마·어휘
        # (ThemeStore·/themes·/types)는 남되 적재 경로는 이 하나다. theme_id 는 무시.
        res = self.jobs.run_sync("ingest:fragment",
                                 lambda: frag.merge_fragment(body, store=self.store))
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
            owner = _req(body, "owner")
            res = self.jobs.run_sync("ingest:pack",
                                     lambda: share.import_share_pack(
                                         body.get("pack") or {}, owner=owner, store=self.store))
            return 200, res
        if engine == "fragment":
            # MCP vinc_ingest(D11 §5) — 단일 write 표면 유지를 위해 잡 큐 직렬화(R4).
            # fragment MERGE 는 짧은 뮤테이션이라 동기(run_sync→200).
            res = self.jobs.run_sync("ingest:fragment",
                                     lambda: frag.merge_fragment(body, store=self.store))
            return 200, res
        fn = self.ingest.get(engine)
        if fn is None:
            return 400, {"error": f"unknown ingest engine: {engine}"}
        job = self.jobs.submit(f"ingest:{engine}", lambda: fn(self.store, body))
        return 202, job.to_dict()

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


def _int(val, default: int) -> int:
    try:
        return int(val) if val is not None else default
    except (TypeError, ValueError):
        return default


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

    def _dispatch(self, method: str):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        # /health — 무인증 준비성 프로브.
        if method == "GET" and path == "/health":
            return self._send(200, {"status": "ok"})

        # 인증(F4): Bearer 세션 토큰 필수.
        if not self.app.auth.check_header(self.headers.get("Authorization")):
            return self._send(401, {"error": "unauthorized"})

        try:
            if method == "GET":
                params = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
                code, obj = self._route_get(path, params)
            else:
                code, obj = self._route_post(path, self._body())
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
            "/atlas": app.read_atlas, "/share": app.read_share,
            "/coupling": app.read_coupling, "/promote-candidates": app.read_promote,
            "/suggest-ties": app.read_suggest,
            "/learner": app.read_learner, "/graph": app.read_graph,
            "/search": app.read_search, "/decide": app.read_decide,
            "/themes": app.read_themes, "/types": app.read_types,
        }
        if path in table:
            return table[path](p)
        if path.startswith("/plan/"):
            return app.read_plan(path[len("/plan/"):], p)
        if path.startswith("/jobs/"):
            return app.get_job(path[len("/jobs/"):])
        return 404, {"error": f"no such route: GET {path}"}

    def _route_post(self, path: str, body: dict):
        app = self.app
        table = {
            "/pin": app.write_pin, "/tag": app.write_tag, "/touch": app.write_touch,
            "/activate": app.write_activate, "/backup": app.submit_backup,
            "/ingest": app.write_ingest,
            "/themes": app.write_theme_create, "/themes/activate": app.write_theme_activate,
            "/types/ratify": app.write_type_ratify, "/types/reject": app.write_type_reject,
            "/types/rename": app.write_type_rename,
        }
        if path in table:
            return table[path](body)
        if path.startswith("/ingest/"):
            return app.submit_ingest(path[len("/ingest/"):], body)
        return 404, {"error": f"no such route: POST {path}"}


# ── 서버 구성 · 수명주기(E6) ─────────────────────────────────────────────────

def make_server(app: VincApp, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    """루프백 바인딩 threaded 서버. port=0 → 임의 포트(반환된 server_address 로 확인)."""
    httpd = ThreadingHTTPServer((host, port), _Handler)
    httpd.app = app                 # 핸들러가 self.server.app 로 접근
    httpd.daemon_threads = True
    return httpd


def _heartbeat(parent_pid: int, stop: threading.Event, interval: float = 1.0):
    """부모(셸) 프로세스 생존 감시. 사망 시 stop 을 세워 clean shutdown 유발(E6)."""
    while not stop.wait(interval):
        try:
            os.kill(parent_pid, 0)   # 신호 0 = 생존 확인(전송 안 함)
        except OSError:
            stop.set()
            return


def serve(cfg=None, *, host: str = "127.0.0.1", port: int = 0, parent_pid: int | None = None,
          token: str | None = None, packs_dir=None, profile_path=None,
          print_info: bool = True) -> dict:
    """사이드카를 기동해 종료 신호까지 서비스한다(블로킹). 기동 정보(host/port/token)를
    stdout 에 1줄 JSON 으로 내보내 셸이 읽는다. SIGTERM/SIGINT 또는 부모 사망 시 정리."""
    app = VincApp.open(cfg, auth=SessionAuth(token), packs_dir=packs_dir,
                       profile_path=profile_path)
    httpd = make_server(app, host, port)
    bound_host, bound_port = httpd.server_address[:2]
    info = {"host": bound_host, "port": bound_port, "token": app.auth.token,
            "driver": getattr(app.store, "driver_name", None)}
    if print_info:
        sys.stdout.write(json.dumps(info, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    if parent_pid:
        threading.Thread(target=_heartbeat, args=(parent_pid, stop), daemon=True).start()

    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        stop.wait()
    finally:
        httpd.shutdown()
        app.close()
    return info
