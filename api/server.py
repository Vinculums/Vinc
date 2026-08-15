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

#: 쿼리 임베딩 대기 상한(초). 조회 요청 **안**에서 도는 호출이라 짧게 잡는다 —
#: 워커가 배경 임베딩으로 바쁘면 기다리느니 FTS 로 답하는 편이 낫다(§11 P2 게이트).
_QVEC_TIMEOUT = float(os.environ.get("VINC_EMBED_ENCODE_TIMEOUT", "1.5"))

from store.config import load_config
from store.backend import open_store

from . import query as q
from . import preference as pref
from . import activate as act
from . import fragment as frag
from . import share
from . import sync
from . import docstore
from . import docsync
from . import graphmd
from . import textextract
from .auth import SessionAuth
from .backup import make_backup
from .jobs import JobQueue, JobError
from store.persistence import SnapshotPolicy, save_snapshot


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
        self._snapshots = SnapshotPolicy()
        self.jobs = jobs or JobQueue(on_idle=self._maybe_snapshot)
        self.packs_dir = packs_dir
        self.profile_path = profile_path
        self.ingest = ingest if ingest is not None else dict(DEFAULT_INGEST)
        self._sync_running = False            # 동시 sync 거부 플래그(S2, 커서 경합 방지)
        # 주제·타입 어휘 스토어(그래프 밖 메타 — <data_dir>.themes.json, DB 파일 형제).
        # D11-R2. 그래프(store)와 분리 — 어휘는 사용자 저작, 그래프는 인스턴스.
        from store.theme_store import ThemeStore
        self.themes = themes if themes is not None else ThemeStore(self.cfg.data_dir)
        self._conn_lock = threading.Lock()    # reader Connection 파생 가드
        self._reader_takes_writer: bool | None = None  # 스토어가 writer= 를 받는가(1회 판정)
        self._mig_note = ""      # 임베딩 모델 이행 중 사유(빈 문자열 = 정상)
        self._mig_at = 0.0       # 그 판정 시각 — 30초 캐시(`_embed_migrating`)
        self._mig_pending = 0    # 이행 중 남은 문서 수(GM-13 배지 진행도, note 와 동시 갱신)
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
            # 앱은 `vinc-sidecar serve` 만 띄운다) 미프로비저닝 스토어면 모든 적재가
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
                print(f"vinc-sidecar: provision failed — {provision_error}", file=sys.stderr)
        return cls(cfg, store=store, provision_error=provision_error, **kw)

    # ── 영속 스냅샷 ──────────────────────────────────────────────────────
    def _maybe_snapshot(self, *, force: bool = False) -> str | None:
        """때가 됐으면 작업본을 영속 볼륨으로 복사한다. **잡 워커 스레드 전용.**

        주기가 곧 최대 손실 창이다(기본 300초). 쓰기가 있었을 때만 뜬다 — 읽기만
        오가는 동안 수십 MB 를 계속 미는 것은 비용만이다."""
        if not force:
            # 잡이 하나 끝나서 불렸다 = 쓰기가 있었을 수 있다. 잡 큐가 곧 쓰기
            # 경로이므로 표시를 호출부에 흩뿌리지 않고 여기서 한 번에 든다.
            self._snapshots.mark_dirty()
            if not self._snapshots.due():
                return None
        state = save_snapshot(self.store)
        self._snapshots.done(state)
        if state.startswith("error"):
            print(f"vinc-sidecar: store snapshot — {state}", file=sys.stderr, flush=True)
        return state

    def mark_store_dirty(self) -> None:
        """쓰기가 있었음을 알린다 — 다음 유휴 시점에 스냅샷 대상이 된다."""
        self._snapshots.mark_dirty()

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
        index_build_failed·algo 실패 note 로 표면화한다). 실측 소요는 1초 미만."""
        return self.jobs.run_sync(label, fn, timeout=timeout)

    def _read(self, fn):
        """읽기 경로 공통: reader 뷰를 열어 fn(reader)->dict 실행 후 닫는다."""
        r = self._reader()
        try:
            return 200, fn(r)
        finally:
            if r is not None:
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
                print(f"vinc-sidecar: final snapshot — {state}", file=sys.stderr, flush=True)
            self.store.close()
            self.store = None

    # ── 읽기 라우트(CLI 1:1) ──────────────────────────────────────────────
    def read_recall(self, p) -> tuple[int, dict]:
        return self._read(lambda r: q.recall(
            p.get("query", ""), owner=p.get("owner"), domain=p.get("domain"),
            tenant=p.get("tenant"), workspace=p.get("workspace"),
            rank=p.get("rank", "recency"), tags=_csv(p.get("tags")),
            limit=_int(p.get("limit"), 10), store=r, debug=_flag(p.get("debug"))))

    def read_domains(self, p):
        return self._read(lambda r: q.domains(owner=p.get("owner"), store=r,
                                              debug=_flag(p.get("debug"))))

    def read_learner(self, p):
        return self._read(lambda r: q.learner_model(owner=_req(p, "owner"), store=r))

    def read_graph(self, p):
        # seed 지정 시 seed+depth ego 서브그래프(View L2 = MCP vinc_graph 동일 경로).
        # layout = 프리셋(radial·circle·tree·organic·rank·dendro — 미지 값 radial 강등).
        # basis(D72) = 시간축. 미지 값은 조용히 recorded 로 강등하고 응답이 에코한다.
        return self._read(lambda r: q.graph(owner=_req(p, "owner"),
                                            domain=p.get("domain"), seed=p.get("seed"),
                                            depth=_int(p.get("depth"), 2),
                                            layout=p.get("layout") or "radial",
                                            basis=p.get("basis") or q.BASIS_RECORDED,
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
        kind = (p.get("kind") or "components").lower()
        owner = _req(p, "owner")
        if kind == "community_context":
            return self._read(lambda r: q.community_context(
                owner=owner, node=_req(p, "node"), store=r,
                member_limit=_int(p.get("member_limit"), 0) or None))
        if kind == "communities":
            return self._read(lambda r: q.communities(
                owner=owner, store=r, member_limit=_int(p.get("member_limit"), 0) or None))
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

        타임아웃이 짧은 이유: 이 호출은 **조회 요청 안**에 있다. 워커가 배경 임베딩으로
        바쁘면 기다리느니 FTS 로 답하는 편이 낫다(§11 P2 게이트). 강등은 응답 메타에
        남으므로 조용하지 않다.

        ⚠ **토큰을 실어야 한다**(2026-08-07). 창구가 루프백일 때는 인증이 없었는데,
        모델을 `vinc-encode` 로 분리하면서 인증이 붙었다. 배치 워커만 고치고 이 경로를
        빠뜨려 **질의 임베딩이 전부 401** 이었다 — 배경 적재는 정상이라 겉으로는 다
        되는 것처럼 보였다. `vector_note` 에 사유가 남아 그나마 드러났다."""
        url = os.environ.get("VINC_EMBED_ENCODE", "").strip()
        if not url or not text:
            return None, ""
        # 모델 이행 중에는 **벡터 팔을 아예 쓰지 않는다**(발주자 결정: 안전한 방향).
        # 이행 구간에는 새 모델로 채워진 문서와 아직 빈 문서가 섞여 있어, 벡터 팔이
        # "이미 처리된 문서" 쪽으로 결과를 기울인다. 커버리지가 반쪽인 채로 순위에
        # 끼는 것보다 FTS 로만 답하는 편이 정직하다. 끝나면 저절로 풀린다.
        mig = self._embed_migrating()
        if mig:
            return None, mig
        try:
            body = json.dumps({"texts": [text], "kind": "query"}).encode("utf-8")
            headers = {"Content-Type": "application/json"}
            tok = os.environ.get("VINC_EMBED_ENCODE_TOKEN", "").strip()
            if tok:
                headers["Authorization"] = f"Bearer {tok}"
            req = urllib.request.Request(url.rstrip("/") + "/encode", data=body,
                                         headers=headers)
            with urllib.request.urlopen(req, timeout=_QVEC_TIMEOUT) as resp:
                vecs = (json.load(resp) or {}).get("vectors") or []
            return (list(vecs[0]), "") if vecs else (None, "encode returned no vector")
        except Exception as exc:
            return None, f"{type(exc).__name__}: {str(exc)[:120]}"

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
        # 적재 단일화(발주자 결정 2026-07-11 → 2026-07-19 개정) — `/ingest` 는
        # fragment 단일 경로(api/fragment.merge_fragment)로 수렴한다. 구 휴면이던
        # 개방층(D11-R2 Entity/TYPED)은 이 라이브 경로에 **포팅**되어 재활성화 —
        # revstamp·SEC-3·dedupe 를 승계한 채 theme 어휘 편입(themes 전달)이 산다.
        # engines/fragment_ingest.py 는 계보 참조용으로만 남는다(호출부 없음).
        cap = _pop_max_domains(body)
        res = self.jobs.run_sync("ingest:fragment",
                                 lambda: frag.merge_fragment(body, store=self.store,
                                                             themes=self.themes,
                                                             max_domains=cap))
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
        # 도메인 이전(vincs.app→vincs.io): 두 오리진을 모두 허용한다. 기존 배포 데스크탑
        # 클라이언트는 vincs.app/connect-app 를, 신규는 vincs.io 를 연다 — 병행 필수.
        # VINC_CONNECT_ORIGINS(콤마 구분)로 오버라이드 가능.
        allowed = [o.strip() for o in os.environ.get(
            "VINC_CONNECT_ORIGINS", "https://vincs.app,https://vincs.io").split(",") if o.strip()]
        if origin and origin not in allowed:
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
    # 편집 UX(웹UI·데스크탑)의 백엔드: get→편집→put 왕복. 쓰기는 잡 큐(run_sync)로
    # 단일 writer 직렬화(재청킹·인덱스 drop 이 다른 쓰기와 경합하지 않게). SEC-1: owner
    # 는 원격/웹에선 MCP 가 토큰에서 실어 강제, 로컬 단일 사용자는 미전송 가능.
    # ⚠ 파생 노드(Concept) 재도출의 자동/수동 정책은 **별도 DEC**(제품 결정) — 여기선
    # 정본 텍스트 CRUD 만 노출하고, MENTIONS 재링크는 put_doc 이 doc 단위로 수행한다.
    def read_doc(self, p):
        return self._read(lambda r: docstore.get_doc(r, _req(p, "id"),
                                                     owner=p.get("owner") or None))

    def read_node_context(self, p):
        # D45-V — 뷰 노드 클릭 상세: 노드 설명(one_liner/summary) + 정본 근거 발췌.
        return self._read(lambda r: q.node_context(
            owner=_req(p, "owner"), node=_req(p, "node"),
            limit=_int(p.get("limit"), 4), store=r))

    def read_doc_highlights(self, p):
        # D45-V2 2단계 — Reader 전문/발췌 내 개념 언급 위치(밑줄 char 범위).
        return self._read(lambda r: docstore.doc_highlights(
            r, _req(p, "doc_id"), _req(p, "concept_id"),
            owner=p.get("owner") or None))

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
        return self._read(lambda r: docstore.list_docs(
            r, owner=p.get("owner") or None, limit=lim,
            cursor=p.get("cursor") or None,
            detail=(p.get("detail") or "full")))

    def read_conventions(self, p):
        """관례(GRAPH.md) 조회 — 어시스턴트가 **쓰기 전에** 읽는 채널(vinc.c.graph-md §6.2).

        스토어에 관례 문서가 있으면 그 본문을, 없으면 **생성 구역만 채운 기본 템플릿**을
        돌려준다. 사용자가 아무것도 안 해 뒀어도 도메인·재가 어휘는 전달된다 — 이 기본본이
        없으면 '아직 안 만든 사람'에게는 이 도구가 그냥 빈손이고, 그 경우가 대다수다."""
        owner = p.get("owner") or None
        lang = p.get("lang") or None

        def _run(r):
            got = docstore.get_doc(r, graphmd.CONVENTIONS_DOC_ID, owner=owner)
            if got.get("executed") and (got.get("text") or "").strip():
                return {"kind": "conventions", "executed": True, "source": "doc",
                        "doc_id": graphmd.CONVENTIONS_DOC_ID, "text": got["text"],
                        "updated_at": got.get("updated_at") or ""}
            built = graphmd.build(r, owner=owner, themes=self.themes, lang=lang,
                                  day=_dt.date.today().isoformat())
            return {"kind": "conventions", "executed": True, "source": "default",
                    "missing": True, "doc_id": graphmd.CONVENTIONS_DOC_ID,
                    "text": built.get("text") or ""}
        return self._read(_run)

    def read_nav_tree(self, p):
        # D69 — 좌측 문서 트리: 주제 → 문서(개념 계층 깊이순) + 미분류.
        return self._read(lambda r: q.nav_tree(owner=_req(p, "owner"), store=r))

    def write_doc_put(self, body):
        owner = body.get("owner") or None
        if not owner:
            return 400, {"error": "owner required — 정본은 무소유로 두지 않는다(SEC-1)"}
        # content_time(D72)은 **키가 없을 때와 빈 값일 때가 다르다**: 키가 없으면
        # 기존 값을 보존하고, 빈 문자열이면 사용자가 지운 것이다. None 으로 뭉개면
        # 제목만 고쳐도 내용 시점이 되살아난다.
        ctime = body.get("content_time")
        res = self.jobs.run_sync("doc.put", lambda: docstore.put_doc(
            self.store, owner=owner, text=_req(body, "text"),
            doc_id=body.get("id") or None, title=body.get("title") or "",
            origin=body.get("origin") or "",
            content_time=None if ctime is None else str(ctime)))
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
            got = docstore.get_doc(self.store, graphmd.CONVENTIONS_DOC_ID, owner=owner)
            existing = got.get("text") if got.get("executed") else None
            built = graphmd.build(self.store, owner=owner, themes=self.themes,
                                  existing=existing, lang=lang,
                                  day=_dt.date.today().isoformat())
            if not built.get("executed"):
                return built
            put = docstore.put_doc(self.store, owner=owner, text=built["text"],
                                   doc_id=graphmd.CONVENTIONS_DOC_ID, title="GRAPH.md",
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
                # ⚠ 여기 오래 `build_search_indexes` 가 있었고 **끝까지 터졌다**.
                # 이 경로는 `_drop_vector_index_if_any` 로 **벡터만** 드롭한다(FTS 를
                # 쓰기마다 드롭했더니 임베딩 도는 동안 키워드 검색이 죽었다 —
                # docstore.py:297 의 2026-07-31 실측). 그런데 `build_search_indexes`
                # 는 'already exists' 를 흡수하지 않으므로 살아 있는 FTS 색인 위에서
                # `Binder exception: Index chunk_ft already exists in table Chunk` 로
                # 던진다(2026-08-09 linux/amd64 실엔진 재현).
                # 게다가 그 예외는 CREATE_VECTOR_INDEX 가 **성공한 뒤** 나므로,
                # 임베딩도 벡터 색인도 다 만들어 놓고 결과만 오류로 돌려주고 있었다.
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
        # U2 — 관계 근거(E-G6)의 **생산자**. 스키마에 열만 있고 채우는 곳이 없어
        # 관계 패널이 언제나 "근거 없음"이었다. 정본 청크에서 두 개념이 같은 문장에
        # 함께 나온 자리를 결정론으로 찾아 채운다(LLM 없음). 이미 근거가 있는
        # 엣지는 건드리지 않으므로 반복 실행이 안전하다.
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

        전환은 쓰기라 잡 큐로 보낸다(R4). 멱등이라 매 라운드 불려도 첫 번째만 일한다."""
        model = (p.get("model") or "").strip()
        if model:
            try:
                self.jobs.run_sync(
                    "embed.model", lambda: docstore.switch_embed_model(self.store, model),
                    timeout=120.0)
            except Exception as exc:      # 전환 실패가 임베딩 자체를 막지는 않는다
                print(f"vinc-sidecar: embed model switch failed — {exc}", file=sys.stderr)
        return self._read(lambda r: {
            "kind": "embed.pending",
            "chunks": docstore.pending_embed_chunks(r, limit=_int(p.get("limit"), 256))})

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

    def write_embed_apply(self, body):
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
                                                                 max_domains=cap))
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
        return self._read(lambda r: docsync.export_docs(
            r, owner=owner, since=_int(p.get("since"), 0)))

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


def _int(val, default: int) -> int:
    try:
        return int(val) if val is not None else default
    except (TypeError, ValueError):
        return default


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
                body = {"status": "degraded", "error": "no reachable store"}
                # ⚠ **사유를 여기까지 들고 온다.** `EmbeddedBackend.open` 은 실패
                # 사유를 `last_open_error` 에 담고 stdout 에도 찍지만, 여태 그걸
                # **읽는 곳이 코드 어디에도 없었다** — /health 는 "no reachable
                # store" 한 줄뿐이라 앱은 "지도를 불러오지 못했습니다" 밖에 말할
                # 수 없었다. 그 화면은 **빈 그래프와 구분되지 않는다.** 실제로
                # 2026-08-14 에 확장 경로 문제로 스토어가 안 열렸는데, 사이드카
                # stdout 을 직접 보기 전까지 정체를 알 수 없었다.
                try:
                    from store.embedded_backend import EmbeddedBackend
                    if EmbeddedBackend.last_open_error:
                        body["open_error"] = EmbeddedBackend.last_open_error
                except Exception:   # noqa: BLE001 — 진단이 헬스를 죽이지 않는다
                    pass
            else:
                # 임베딩 백로그 — 정체가 밖에서 보여야 한다(App.embed_health 주석).
                # 진단이 헬스를 죽이지 않게 통째로 감싼다: 여기서 예외가 나가면
                # 엔트리포인트의 기동 대기가 실패하고 워치독이 사이드카를 재기동시킨다.
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
            # 관례(GRAPH.md) — 어시스턴트가 쓰기 전에 읽는 채널(vinc.c.graph-md).
            "/conventions": app.read_conventions,
            "/nav-tree": app.read_nav_tree,   # 좌측 문서 트리(D69)
            "/doc/highlights": app.read_doc_highlights,   # Reader 밑줄(D45-V2 2단계)
            "/node-context": app.read_node_context,
            "/edge-context": app.read_edge_context,   # 흐름 선 클릭(D45-V 1단계)
            "/ledger-drilldown": app.read_ledger_drilldown,   # 합계 구간 클릭(D45-V2 3단계)
            "/docs-in-range": app.read_docs_in_range,   # 타임라인 기간 브러시(D45-V2 4단계)
            # 동기화(S1) — 선택 도메인 조회 · delta 번들 export.
            "/sync/domains": app.read_sync_domains,
            "/sync/pull-delta": app.read_sync_pull_delta,
            "/sync/docs-export": app.read_sync_docs_export,   # 문서 정본 delta(D45 P3)
            "/embed/pending": app.read_embed_pending,   # 자체호스팅 임베딩 워커(B, 내부)
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

    def _route_post(self, path: str, body: dict):
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
            # 계정 연결(디바이스 플로우) — state 발급(인증). callback 은 _dispatch 의
            # 무인증 예외 경로(여기 표에 없음 — 표는 인증 이후에만 닿는다).
            "/connect/start": app.write_connect_start,
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


def _pid_alive(pid: int) -> bool:
    """부모 PID 생존 확인 — **조회 전용, 대상을 죽이지 않는다.**

    POSIX 는 ``os.kill(pid, 0)`` 이 신호 없는 생존 확인이지만, **Windows 의 os.kill 은
    TerminateProcess 로 매핑되는 파괴적 관용구**라 부모(셸)를 죽일 수 있다(CPython
    버전·빌드별 거동 상이 — 실측). 그래서 Windows 에서는 ``OpenProcess(SYNCHRONIZE)``
    + ``WaitForSingleObject(0)`` 로 **조회만** 한다: 핸들을 못 열거나(=종료·부재) 신호
    상태(=종료)면 사망으로 본다. 오판은 fail-safe 방향(사이드카 자가종료)일 뿐 셸을
    건드리지 않는다. 이 명시 경로가 있어 셸(``main.rs``)이 ``--parent-pid`` 를 안전히
    넘길 수 있다."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        SYNCHRONIZE = 0x00100000
        WAIT_TIMEOUT = 0x00000102   # 미신호 → 아직 실행 중
        k32 = ctypes.windll.kernel32
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.WaitForSingleObject.restype = wintypes.DWORD
        k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = k32.OpenProcess(SYNCHRONIZE, False, pid)
        if not handle:
            return False
        try:
            return k32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
        finally:
            k32.CloseHandle(handle)
    try:
        os.kill(pid, 0)   # POSIX: 신호 0 = 생존 확인(전송 안 함)
        return True
    except OSError:
        return False


def _heartbeat(parent_pid: int, stop: threading.Event, interval: float = 1.0):
    """부모(셸) 프로세스 생존 감시. 사망 시 stop 을 세워 clean shutdown 유발(E6)."""
    while not stop.wait(interval):
        if not _pid_alive(parent_pid):
            stop.set()
            return


def serve(cfg=None, *, host: str = "127.0.0.1", port: int = 0, parent_pid: int | None = None,
          token: str | None = None, packs_dir=None, profile_path=None,
          print_info: bool = True) -> dict:
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
    httpd = make_server(app, host, port)
    bound_host, bound_port = httpd.server_address[:2]
    shown = f"<supplied:{len(app.auth.token)}ch>" if token else app.auth.token
    info = {"host": bound_host, "port": bound_port, "token": shown,
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
