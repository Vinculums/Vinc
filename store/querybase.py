# -*- coding: utf-8 -*-
"""질의 계획 실행과 그 바닥 보조 함수 — api 와 vinc_rag 가 함께 쓰는 층.

`api/query.py` 에서 원문 그대로 옮겼다(VIN-243). 계획 묶음(`QueryPlan`)과 실행(`execute`),
도메인 하위 트리 조건, 유효구간(valid_from/valid_until) 판정, 질문 속 시점 해석, 질의 토큰
정규화, 관례 문서(GRAPH.md) id 판정이 여기 있다. 위 층(`api`·`vinc_mcp`·`vinc_rag`)을
import 하지 않는다 — `scripts/gate_ownership.py` 의 [LAYER] 가 막는다.
`api/query.py` 가 같은 이름을 다시 내보내므로 `q.execute` 같은 기존 호출은 그대로다.
근거: [[vinc.plan.team-split]]
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from dataclasses import dataclass, field
from store.config import StoreConfig, load_config
from store.backend import open_store


@dataclass
class QueryPlan:
    kind: str            # recall | search | decide
    cypher: str
    params: dict = field(default_factory=dict)
    note: str = ""

    def to_dict(self) -> dict:
        return {"kind": self.kind, "cypher": self.cypher, "params": self.params, "note": self.note}


def execute(plan: QueryPlan, cfg: StoreConfig | None = None, *, store=None,
            debug: bool = False) -> dict:
    """Run a plan if a store is reachable; otherwise return the plan only.

    백엔드 중립(설계 §4-3): `open_store(cfg)` 가 None(도달 불가)이면 순수 플랜을
    `executed=False` 로 반환 — DB 부재 시 결정론 계약 유지(§1 불변 원칙).

    ``store`` 주입(사이드카 §11 P2): 호출부가 이미 연 스토어(잡 워커의 writer 또는
    read 뷰)를 넘기면 그것을 재사용한다 — 이때는 **열지도 닫지도 않는다**(수명은
    호출부 소유). 미주입(기본)이면 종전대로 호출당 open→close 한다(기존 계약 불변)."""
    cfg = cfg or load_config()
    owns = store is None
    if owns:
        store = open_store(cfg)
    # 🔴 F3 — **plan 은 기본으로 싣지 않는다**(cypher 전문 + owner UUID = 토큰 낭비이자
    # 내부 노출). 진단이 필요한 자리는 따로 있다 — `/plan/*` 라우트와 CLI `plan` 은
    # 빌더를 직접 부르므로 이 경로를 안 탄다.
    # F4 — `kind` 는 **항상** 실린다. 호스트가 응답을 코드로 다루려면 종류 표시가 늘
    # 있어야 한다([[vinc.plan.mcp-hardening]]).
    base = {"kind": plan.kind, "executed": False, "rows": []}
    if debug:
        base["plan"] = plan.to_dict()
    if store is None:
        # 예전엔 "dry plan returned" 였다. plan 을 안 실으면서 그 문구를 남기면
        # 호출부가 없는 필드를 찾는다.
        base["note"] = ("no reachable store"
                        + (" (dry plan returned)" if debug else ""))
        return base
    try:
        rows = store.run(plan.cypher, plan.params)
        base["executed"] = True
        base["rows"] = rows
    except Exception as exc:
        # 강등(graceful degradation, §3-4): 스토어가 플랜을 실행하지 못해도 던지지
        # 않는다(무 크래시 계약). 대신 실패를 **최상위 `error`** 로 말한다 — 빈 rows 만
        # 돌려주면 "결과가 없다"와 구별되지 않는다(recall 은 has_more:false 까지 붙어
        # 끝 페이지로 읽혔다). `note` 는 여러 호출부가 자기 응답으로 옮겨 싣는 자리라
        # 사유를 계속 담는다. plan 은 debug 일 때만 실린다(위 F3).
        # 근거: [[vinc.audit.rag-code-review-2026-09-29]] 22번
        reason = f"{type(exc).__name__}: {exc}"
        base["error"] = f"store query failed: {reason}"
        base["note"] = f"store 실행 실패: {reason}" + (" (dry plan returned)" if debug else "")
    finally:
        if owns:
            store.close()   # 주입 스토어는 호출부가 소유 — 여기서 닫지 않는다.
    return base


def domain_counts_plan(*, owner: str | None = None) -> QueryPlan:
    """`/domains` 의 Episode 편향 해소(C2) — Episode 만 집계하면 Concept·Record
    위주 도메인이 목록에서 통째로 빠진다(실사용 사고 원인). Episode·Concept·Record
    3개 노드 테이블을 도메인별로 각각 집계해 ``kind`` 태그와 함께 한 결과셋으로
    담는다 — 합산·정렬은 :func:`domains`(순수 후처리)가 맡는다.

    단일 Cypher(UNION ALL) — Kùzu 0.11.3 실측 지원 확인(2026-07): 서로 다른 라벨의
    MATCH 절을 UNION ALL 로 이어도 파라미터 공유·리터럴 컬럼이 그대로 동작한다.

    무주제 노드도 **센다** — WHERE 는 owner 스코프만 건다. domain 이 NULL·'' 인
    행은 :func:`domains` 가 "(none)" 한 줄로 합친다. 여기서 걸러 내면 무주제는
    0이 아니라 부재로 읽혀, 센서스가 자기가 못 세는 것을 숨긴다.
    근거: [[vinc.ops.topic-assignment-eval]] §1.1"""
    if owner:
        where_e = "WHERE e.owner_id = $owner"
        where_c = "WHERE c.owner_id = $owner"
        where_r = "WHERE r.owner_id = $owner"
        where_d = "WHERE d.owner_id = $owner"
        params: dict = {"owner": owner}
    else:
        where_e = where_c = where_r = where_d = ""
        params = {}
    cypher = (
        f"MATCH (e:Episode) {where_e} "
        "RETURN e.domain AS domain, count(*) AS n, 'episodes' AS kind "
        "UNION ALL "
        f"MATCH (c:Concept) {where_c} "
        "RETURN c.domain AS domain, count(*) AS n, 'concepts' AS kind "
        "UNION ALL "
        f"MATCH (r:Record) {where_r} "
        "RETURN r.domain AS domain, count(*) AS n, 'records' AS kind "
        "UNION ALL "
        # v12 — 결정을 세지 않으면 이관 직후 `concepts` 가 68 줄어든 채 결정이
        # 어디에도 안 나온다. 그 화면은 데이터 손실과 구분되지 않는다.
        f"MATCH (d:Decision) {where_d} "
        "RETURN d.domain AS domain, count(*) AS n, 'decisions' AS kind"
    )
    return QueryPlan("domains", cypher, params,
                     note="Episode+Concept+Record 집계 (open-domain discovery, data-driven)")


def domains(*, owner: str | None = None, cfg=None, store=None,
            debug: bool = False) -> dict:
    """도메인 목록(C2) — Episode·Concept·Record·Decision 4종을 모두 세어 `{domain,
    episodes, concepts, records, decisions, total}` 로 병합, total 내림차순 정렬. `episodes` 필드는
    하위호환으로 유지(값 = Episode 카운트만).

    무주제 노드는 "(none)" 행으로 나온다 — 버리면 부재가 비존재로 읽힌다.
    주제 상한 검사(:func:`domain_names`)는 이 행을 세지 않는다(별도 플랜)."""
    res = execute(domain_counts_plan(owner=owner), cfg, store=store, debug=debug)
    if not res.get("executed"):
        return res
    agg: dict[str, dict] = {}
    for row in res.get("rows", []):
        dom = row.get("domain") or "(none)"
        # v12 — `decisions` 를 버킷에 넣지 않으면 아래 `if kind in bucket` 이 그 행을
        # **조용히 버린다**(플랜은 세는데 응답에는 안 나온다).
        bucket = agg.setdefault(dom, {"domain": dom, "episodes": 0, "concepts": 0,
                                      "records": 0, "decisions": 0})
        kind = row.get("kind")
        if kind in bucket:
            bucket[kind] += int(row.get("n") or 0)
    rows = list(agg.values())
    for r in rows:
        r["total"] = r["episodes"] + r["concepts"] + r["records"] + r["decisions"]
    rows.sort(key=lambda r: (-r["total"], r["domain"]))
    res["rows"] = rows
    return res


def domain_names_plan(*, owner: str | None = None) -> QueryPlan:
    """소유자의 **주제 이름 집합**(개수·집계 없음).

    :func:`domain_counts_plan` 과 같은 3개 라벨을 보되 카운트를 세지 않는다 —
    무료 등급 주제 상한이 **매 적재마다** 도는 경로라 집계·정렬이 순수 낭비다.

    라벨 집합을 `domains()` 와 **일부러 일치**시켰다(Episode·Concept·Record·Decision).
    상한이 다른 집합을 세면 사용자가 `vinc_domains` 로 4개를 보면서 "5개 상한에
    걸렸다"는 말을 듣게 된다 — 그 불일치가 곧 지원 문의가 된다."""
    if owner:
        where_e = "WHERE e.owner_id = $owner AND e.domain IS NOT NULL"
        where_c = "WHERE c.owner_id = $owner AND c.domain IS NOT NULL"
        where_r = "WHERE r.owner_id = $owner AND r.domain IS NOT NULL"
        where_d = "WHERE d.owner_id = $owner AND d.domain IS NOT NULL"
        params: dict = {"owner": owner}
    else:
        where_e = "WHERE e.domain IS NOT NULL"
        where_c = "WHERE c.domain IS NOT NULL"
        where_r = "WHERE r.domain IS NOT NULL"
        where_d = "WHERE d.domain IS NOT NULL"
        params = {}
    cypher = (
        f"MATCH (e:Episode) {where_e} RETURN DISTINCT e.domain AS domain "
        "UNION ALL "
        f"MATCH (c:Concept) {where_c} RETURN DISTINCT c.domain AS domain "
        "UNION ALL "
        f"MATCH (r:Record) {where_r} RETURN DISTINCT r.domain AS domain "
        "UNION ALL "
        f"MATCH (d:Decision) {where_d} RETURN DISTINCT d.domain AS domain"
    )
    return QueryPlan("domain_names", cypher, params,
                     note="주제 이름 집합 (상한 검사용 — 집계 없음)")


def domain_names(*, owner: str | None = None, cfg=None, store=None) -> set[str] | None:
    """소유자가 지금 쓰는 주제 이름 집합. 스토어에 못 닿으면 **None**.

    None 과 빈 집합은 다르다 — 빈 집합은 "주제가 없다"(상한 검사 통과)이고 None 은
    "모른다"다. 모르는 채로 상한을 따지면 스토어 장애가 곧 전면 쓰기 거부가 된다.
    호출부가 이 구분을 보고 결정해야 한다."""
    res = execute(domain_names_plan(owner=owner), cfg, store=store)
    if not res.get("executed"):
        return None
    return {d for d in (row.get("domain") for row in res.get("rows", [])) if d}


#: 주제 스코프 = **하위 트리**다(정확일치 OR `d + "/"` 접두 — 슬래시가 요점이다:
#: `STARTS WITH 'vinc'` 는 `vincent` 도 잡는다). 다섯 플랜이 각자 도메인을 걸러서
#: 판정은 여기 **한 벌**만 둔다 — 한 곳만 고치면 축끼리 모집단이 어긋난다.
#: 왜 이게 없어서 상위 주제가 0건이었는지:
#: [[record:query-scope-ignored-the-topic-tree-it-was-standing-on]].
def domain_subtree_where(cols: list[str], domains: list[str], params: dict,
                         *, key: str = "dom") -> str:
    """``cols`` 중 하나가 ``domains`` 중 하나의 하위 트리에 드는가."""
    ors: list[str] = []
    for i, d in enumerate(domains):
        params[f"{key}{i}"] = d
        params[f"{key}p{i}"] = d.rstrip("/") + "/"
        for col in cols:
            ors.append(f"({col} = ${key}{i} OR {col} STARTS WITH ${key}p{i})")
    return "(" + " OR ".join(ors) + ")"


#: 유효구간 투영 한 자리(W13) — Record 의 `valid_from`/`valid_until` 을 읽기 표면이
#: 함께 낸다. 무효화된 주장을 무효화 표식 없이 돌려주면 읽는 쪽이 낡은 것을 참으로
#: 쓴다 — 열만 있고 투영이 없으면 이 기능은 없는 것과 같다.
#:
#: ⚠ 이 두 열은 **Record 에만** 있다(one_liner 의 Concept 동형 — `store/
#: embedded_schema.py` v15). 다른 라벨의 UNION 팔에는 `_VALIDITY_EMPTY` 리터럴로
#: 모양을 맞춘다 — 열을 옮겨 붙이면 Binder 가 죽고 그 조회 전체가 함께 죽는다.
#: 열 목록이 한 벌인 이유는 `_track_cols` 와 같다(`_KEPT_FIELDS` 전례).
#: 근거: [[vinc.plan.0-9-0-open-decisions §2]].
def _validity_cols(alias: str, prefix: str = "") -> str:
    return (f"coalesce({alias}.valid_from, '') AS {prefix}valid_from, "
            f"coalesce({alias}.valid_until, '') AS {prefix}valid_until")


#: Record 아닌 UNION 팔의 자리 맞춤 — 빈 문자열은 결손이 아니라 사실이다
#: (그 종류엔 구간이 없다).
_VALIDITY_EMPTY = "'' AS valid_from, '' AS valid_until"


def _today() -> str:
    """오늘(UTC, `YYYY-MM-DD`). 구간 비교는 사전식이라 문자열이면 충분하다."""
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")


def now_iso() -> str:
    """지금(UTC, 초 단위 `YYYY-MM-DDTHH:MM:SSZ`). 그래프 밖 메타의 시각 문자열은 이 한 벌이다.

    ⚠ 형식을 바꾸지 말 것: `api.docstore._age_s` 가 이 형식 그대로 되읽는다.
    동기화 스탬프(`updated_at`)는 이것이 아니라 밀리초 HLC(`api.revstamp.hlc_now`)다."""
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def split_csv(val) -> list[str] | None:
    """쉼표 구분 값 → 목록(None·'' → None). 이미 목록이면 그대로 돌려준다(HTTP 본문)."""
    if not val:
        return None
    if isinstance(val, list):
        return val
    return [s.strip() for s in val.split(",") if s.strip()]


def validity_state(valid_from: str | None, valid_until: str | None,
                   *, now: str | None = None) -> str:
    """반개구간 `[valid_from, valid_until)` 에서 파생하는 상태.
    `future` · `current` · `historical` 셋 중 하나를 낸다.

    🔴 **미기재 `valid_from` 은 current 다.** 이 한 줄이 이 함수의 전부라 해도 된다.
    정책 초안은 `current = valid_from <= now` 였는데 거기엔 미기재 분기가 없고,
    W13 계약이 "빈 값 = 미기재"인 이상 그건 예외가 아니라 **다수**다 — 실측으로
    Record 610건 중 **432건(70.8%)** 이 `valid_from` 을 안 달고 있다. 그 정의로
    기본 읽기를 걸면 낡은 것이 아니라 코퍼스의 7할이 사라진다. 그래서 무언가를
    감추는 것은 **명시된 `valid_until` 뿐**이고, 그것이 "stale ≠ false"와도
    실제 저작 관행(`valid_until` 사용 7건)과도 맞는다.

    구간 형식은 `YYYY[-MM[-DD]]` 이고 비교는 사전식이다. `2026` 은 `2026-09-09`
    보다 작으므로 연도만 적힌 시작은 그 해 내내 current 이고, 연도만 적힌 끝은
    그 해가 시작되는 순간부터 historical 이다(반개구간의 뜻 그대로).

    근거: [[decision:temporal-model-operational-policy-2026-09-09]] ·
    [[record:time-axis-census-2026-09-11]]"""
    now = now or _today()
    vf = (valid_from or "").strip()
    vu = (valid_until or "").strip()
    if vf and now < vf:
        return "future"
    if vu and now >= vu:
        return "historical"
    return "current"


# ── 시점 읽기(as_of) — "그때 성립하던 것" ──────────────────────────────────────
# 기본 읽기는 **오늘** 성립하는 것을 보여 준다(`validity_state`, historical 만 감춘다).
# `as_of` 는 그 "오늘"을 사람이 고른 기간으로 바꾼다. 기간인 이유: `2026-08` 이라고 물으면
# 한 순간이 아니라 그 달이고, 그 달 동안 한 번이라도 성립한 주장이 답이다. 날짜 하나를
# 주면 그 하루다. 계획의 `holds_at(T)` 를 기간으로 넓힌 것이다.
# ⚠ 기본값에서는 아무것도 안 바뀐다. `as_of` 가 **주어졌을 때만** 시작 전(future)도 감춘다
# — 오늘 읽기에서 future 를 안 감추는 이유(UTC 시계와 KST 저작)는 날짜를 사람이 고른 시점
# 읽기에는 해당하지 않는다.
# 근거: [[decision:temporal-model-operational-policy-2026-09-09]]
_SCOPE_VALUE = re.compile(r"^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?$")


def time_scope(value: str | None) -> tuple[str, str] | None:
    """`YYYY[-MM[-DD]]` → 그 기간의 `[시작, 끝)` (둘 다 `YYYY-MM-DD`). 틀린 값은 None."""
    m = _SCOPE_VALUE.match((value or "").strip())
    if not m:
        return None
    y, mo, d = m.groups()
    try:
        if d:
            start = _dt.date(int(y), int(mo), int(d))
            end = start + _dt.timedelta(days=1)
        elif mo:
            start = _dt.date(int(y), int(mo), 1)
            end = _dt.date(int(y) + (int(mo) == 12), int(mo) % 12 + 1, 1)
        else:
            start = _dt.date(int(y), 1, 1)
            end = _dt.date(int(y) + 1, 1, 1)
    except ValueError:
        return None
    return start.isoformat(), end.isoformat()


def _pad_date(v: str) -> str:
    """구간 끝점을 날짜로 편다: `2026` → `2026-01-01`, `2026-08` → `2026-08-01`.
    사전식 비교에서 짧은 값이 그 기간의 **첫날**로 읽히는 `validity_state` 의 규칙과 같다."""
    v = (v or "").strip()
    if len(v) == 4:
        return v + "-01-01"
    if len(v) == 7:
        return v + "-01"
    return v


def holds_during(valid_from: str | None, valid_until: str | None,
                 scope: tuple[str, str]) -> bool:
    """반개구간 `[valid_from, valid_until)` 이 기간 `[start, end)` 와 겹치는가.
    미기재 시작·끝은 열린 끝이다(미기재는 current 라는 정책 그대로)."""
    start, end = scope
    vf, vu = _pad_date(valid_from or ""), _pad_date(valid_until or "")
    if vf and vf >= end:
        return False            # 기간이 끝난 뒤에 시작했다
    if vu and vu <= start:
        return False            # 기간이 시작되기 전에 끝났다
    return True


# ── 질문 속 시점(EXAQT 방식, 규칙만) ─────────────────────────────────────────
# 질문에 적힌 **절대** 시점을 규칙으로 읽는다(Jia et al. 2021 의 시점 필터를 모델 없이).
# 모호하면 읽지 않는다: 상대 시점("지난주")·전치사 없는 맨 연도(버전·id 와 헷갈린다)는
# 받지 않고, 서로 다른 시점이 둘 이상이면 고르지 않는다 — 고르는 것은 추측이다.
_MONTHS = {m: i for i, names in enumerate((
    ("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"),
    ("may",), ("june", "jun"), ("july", "jul"), ("august", "aug"),
    ("september", "sep", "sept"), ("october", "oct"), ("november", "nov"),
    ("december", "dec")), start=1) for m in names}


_MON = r"(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec)\.?"


_Q_TIME = [
    # 2026-08-15 · 2026.08.15 · 2026/08 — 앞뒤에 ASCII 슬러그 문자가 붙으면 id·버전·경로의
    # 일부다(`record:fact-2026-09-28-x9`). `\w` 로 막지 않는 이유: 한국어 조사("2026-08-15에")
    # 도 `\w` 라서 함께 막힌다.
    ("ymd", re.compile(r"(?<![A-Za-z0-9_.:/-])((?:19|20)\d{2})[-./](\d{1,2})"
                       r"(?:[-./](\d{1,2}))?(?![A-Za-z0-9_-])")),
    # 2026년 8월 15일 · 2026년 8월 · 2025년
    ("ko", re.compile(r"(?<![A-Za-z0-9_.:/-])((?:19|20)\d{2})\s*년"
                      r"(?:\s*(\d{1,2})\s*월(?:\s*(\d{1,2})\s*일)?)?"
                      r"(?![A-Za-z0-9_/-])")),
    # 15 August 2026 — 월-연보다 먼저다. 뒤에 두면 "August 2026" 이 자리를 가져가 일이 빠진다
    ("dmy", re.compile(r"(?<![A-Za-z0-9_.:/-])(\d{1,2})\s+" + _MON
                       + r"\s+((?:19|20)\d{2})\b(?![A-Za-z0-9_/-])", re.I)),
    # August 15, 2026 · August 2026
    ("mdy", re.compile(r"(?<![A-Za-z0-9_.:/-])" + _MON
                       + r"\s+(?:(\d{1,2})(?:st|nd|rd|th)?,?\s+)?"
                       + r"((?:19|20)\d{2})\b(?![A-Za-z0-9_/-])", re.I)),
    # in 2025 · during 2025 · as of 2025
    ("prep", re.compile(r"\b(?:in|during|as of)\s+((?:19|20)\d{2})\b(?![-./]\d)", re.I)),
]


def query_time(query: str) -> list[dict]:
    """질문에서 읽은 절대 시점들. 각 항목은 `{as_of, from, until, text}` 이고 같은 기간은
    한 번만 나온다. 겹치는 자리는 먼저 맞은 규칙이 가진다(`2026-08` 안의 `2026` 을 두 번
    세지 않게)."""
    found: list[dict] = []
    taken: list[tuple[int, int]] = []

    def free(a: int, b: int) -> bool:
        return all(b <= s or a >= e for s, e in taken)

    for kind, rx in _Q_TIME:
        for m in rx.finditer(query or ""):
            if not free(m.start(), m.end()):
                continue
            g = m.groups()
            if kind in ("ymd", "ko"):
                y, mo, d = g[0], g[1], g[2]
            elif kind == "mdy":
                mo, d, y = str(_MONTHS[g[0].lower()]), g[1], g[2]
            elif kind == "dmy":
                d, mo, y = g[0], str(_MONTHS[g[1].lower()]), g[2]
            else:
                y, mo, d = g[0], None, None
            label = y + (f"-{int(mo):02d}" if mo else "") + (f"-{int(d):02d}" if mo and d else "")
            scope = time_scope(label)
            if scope is None:
                continue
            taken.append((m.start(), m.end()))
            if all(f["as_of"] != label for f in found):
                found.append({"as_of": label, "from": scope[0], "until": scope[1],
                              "text": m.group(0)})
    return found


def resolve_as_of(as_of: str | None, query: str) -> tuple[tuple[str, str] | None, dict | None]:
    """`as_of` 인자 → (기간 또는 None, 응답에 실을 설명). 설명이 None 이면 기간이 없다는
    뜻이고 읽기는 오늘 규칙이다. 틀린 값은 ValueError 로 올린다(조용히 오늘로 떨어지면
    호출부는 과거를 읽은 줄 안다)."""
    v = (as_of or "").strip()
    if not v:
        return None, None
    if v.lower() == "query":
        hits = query_time(query)
        if len(hits) == 1:
            h = hits[0]
            return (h["from"], h["until"]), {"as_of": h["as_of"], "from": h["from"],
                                             "until": h["until"], "source": "query",
                                             "text": h["text"]}
        note = ("no absolute date was found in the query, so today's view was used"
                if not hits else
                "the query names more than one date ({}), so none was picked and "
                "today's view was used".format(", ".join(h["as_of"] for h in hits)))
        return None, {"source": "query", "applied": False, "note": note,
                      "found": [h["as_of"] for h in hits]}
    scope = time_scope(v)
    if scope is None:
        raise ValueError(f"invalid as_of {as_of!r}: use YYYY, YYYY-MM, YYYY-MM-DD or 'query'")
    return scope, {"as_of": v, "from": scope[0], "until": scope[1], "source": "argument"}


def _props_to_obj(raw) -> dict:
    """props 정규화(A1.9 A4) — 스토어는 props 를 JSON **문자열**로 저장한다(임베디드
    스키마). 다리(bridge)에서 vinc_ingest 로 바로 되먹일 수 있게 dict 로 파싱한다.
    dict 면 그대로, 문자열이면 json.loads, 실패·null 이면 {}."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            v = json.loads(raw)
            return v if isinstance(v, dict) else {"_": v}
        except Exception:
            return {}
    return {}


def _classify_chunk_error(exc: Exception) -> str:
    """Chunk 검색 실패 사유 분류(T3b-1 — '실패 침묵' 제거의 근거). '인덱스 부재'와
    '확장 미가용'과 '미프로비저닝'을 구분해 응답 error 에 접두로 담는다(실측 문구):
    - index_missing: "Table Chunk doesn't have an index with name chunk_ft"
    - extension_unavailable: `_load_ext` 강등 문구("... 확장 미가용 ...", 오프라인 미번들)
    - not_provisioned: "Table Chunk does not exist"(스키마 선행 안 됨)."""
    m = str(exc).lower()
    if "have an index" in m:                       # doesn't/does not have an index
        return "index_missing"
    if "확장 미가용" in str(exc) or ("extension" in m and "미가용" in str(exc)):
        return "extension_unavailable"
    if "does not exist" in m or ("table" in m and "exist" in m):
        return "not_provisioned"
    return "error"


# ── 질문 정규화 (A1.10) — 자연어 질문을 노드 검색이 탈 수 있는 토큰으로 ──────────
# 토큰 AND 는 "이름을 댄" 질의만 통과시킨다. 조사·의문사는 어느 노드 필드에도 없어서
# 정규화가 없으면 AND 가 **항상 0건**이 되고, 그러면 묻기가 알파벳순 첫 후보를 답으로
# 내놓는다([[vinc.plan.mcp-hardening]]).
#: 의문사·정중어 — 어느 노드에도 없으면서 AND 를 깨뜨리기만 하는 말들.
_STOP_TOKENS = frozenset("""
뭐 뭐야 뭔가 뭔데 무엇 무엇인가 뭡니까 어떻게 어떤 어찌 왜 언제 어디 어디서 누가 누구
얼마나 몇 며칠 인가 인가요 일까 일까요 건가 건가요 하나요 되나요 하죠 알려줘 알려주세요
설명해줘 설명해주세요 정리해줘 보여줘 궁금해 궁금합니다 해줘 해주세요 주세요 대해 대해서
관해 관해서 무슨
what what's whats which who whom whose when where why how is are was were do does did
the a an of about on in to for me tell explain show give please summarize
""".split())


#: 한국어 조사·접미 — 명사 뒤에 붙어 토큰을 다르게 만든다(긴 것부터 벗긴다).
#: 복합 조사(`에서의`·`에는`·`와의` …)는 그 끝 글자보다 **앞에** 둬야 통째로 벗겨진다.
_KO_SUFFIXES = ("이라는", "라는", "에서는", "에게서", "으로는", "에서의", "으로의", "에서", "에게",
                "한테", "부터", "까지", "보다", "처럼", "으로", "이란", "라면", "이는", "과는",
                "와는", "에는", "와의", "과의", "은", "는", "이", "가", "을", "를", "의", "에",
                "도", "만", "로", "와", "과", "란", "야")


def _strip_ko_suffix(tok: str) -> str:
    """토큰 끝의 조사를 한 번 벗긴다 — 남는 어간이 2자 이상일 때만.
    '제어가'→'제어', '환불은'→'환불'. '국가'처럼 어간이 1자로 줄 것은 손대지 않는다.

    형태소 분석이 아니라 접미 절단이라 '전문가'→'전문' 같은 **과잉 절단**이 난다.
    그래도 무해한 이유: 매칭이 `CONTAINS`(부분 문자열)라 짧아진 어간은 원래 낱말을
    여전히 포함 매치한다('전문'은 '전문가'의 부분 문자열). 즉 과잉 절단은 재현율을
    넓히지 저격 대상을 놓치지 않는다 — 반대로 **절단 부족**이 이 함수가 생긴 이유다
    (조사 하나 때문에 토큰 AND 가 통째로 0건이 됐다). 사전 없는 결정론을 유지한다."""
    for suf in _KO_SUFFIXES:
        if len(tok) > len(suf) + 1 and tok.endswith(suf):
            return tok[: -len(suf)]
    return tok


#: 질문을 낱말로 가르는 경계 문자류. 질의 쪽(`normalize_query_tokens`)과 색인 쪽 조사 벗기기
#: (`rag_eval.rerank.ko_stripped_text`)가 **이 한 벌**을 쓴다 — 두 쪽이 다르게 자르면 그
#: 차이가 곧 결과를 바꾼다.
QUERY_SPLIT_RE = re.compile(r"[\s,./?!;:()\[\]{}\"'`~]+")


def normalize_query_tokens(query: str, *, cap: int = 8) -> list[str]:
    """질문 → 검색 토큰(순수·결정론). 구두점 제거 → 소문자 → 조사 절단 → 불용어 제거.

    **불용어를 걷어낸 뒤 아무것도 안 남으면 걷어내기 전으로 되돌린다** — "뭐야" 한
    마디만 물었을 때 질의가 통째로 사라져 전 노드를 삼키는 것을 막는다(빈 토큰은
    호출부가 조기 반환한다). 조사 절단본과 원형을 **둘 다** 남기지는 않는다:
    중복 토큰은 매치 수 집계를 왜곡한다."""
    raw = [t for t in QUERY_SPLIT_RE.split((query or "").lower()) if t]
    stemmed = [_strip_ko_suffix(t) for t in raw]
    # 불용어는 원형과 절단형 **둘 다** 본다 — 절단이 먼저면 '무엇인가'가 '무엇인'이 되어
    # 목록을 빠져나간다.
    kept = [s for t, s in zip(raw, stemmed)
            if s and t not in _STOP_TOKENS and s not in _STOP_TOKENS]
    if not kept:                      # 전부 불용어였다 — 원형으로 되돌린다
        kept = [t for t in stemmed if t]
    out: list[str] = []
    for t in kept:                    # 순서 유지 dedup
        if t not in out:
            out.append(t)
    return out[:cap]


#: owner 차원이 없던 시절의 고정 doc_id. **한 owner 가 아직 이 자리를 쥐고 있다** —
#: `_DocMeta` 가 doc_id 단독으로 키를 잡으므로 이 문자열의 행은 전 시스템에 하나뿐이고,
#: 먼저 쓴 owner 가 영구 점유한다. 그래서 둘째 테넌트는 읽기·쓰기 양쪽에서 owner
#: mismatch 를 받았다([[vinc.ops.risk-audit-2026-08-05 §3.4]], 2026-09-14 재현).
#: 지금은 **새로 이 자리를 겨냥하는 경로가 없고**(아래 conventions_doc_id), 이미 쥔
#: owner 만 계속 쓴다 — 그 행을 옮기지 않는 이유는 옮김이 곧 마이그레이션이고
#: 이 리포가 키 재작성으로 하드다운을 겪었기 때문이다([[vinc.plan.node-pk-namespacing]] §3).
LEGACY_CONVENTIONS_DOC_ID = "graph.md"

#: owner 파생 관례 id 의 접두. 뒤에 owner 해시 16 자가 붙는다.
_CONVENTIONS_PREFIX = LEGACY_CONVENTIONS_DOC_ID + "~"

_OWNER_TAG_RE = re.compile(r"^[0-9a-f]{16}$")

def is_conventions_doc_id(doc_id: str) -> bool:
    """관례 문서인가 — 구 고정 id 와 owner 파생 id **둘 다**.

    문서 목록·좌측 트리·동기화·검색이 관례를 걸러내는 판정이 전부 이 한 자리다.
    한 곳이라도 구 리터럴 동등비교로 남으면 그쪽만 조용히 새 id 를 놓친다(목록에
    관례가 섞여 나오거나, 기기와 계정이 서로의 관례를 덮어쓴다)."""
    if doc_id == LEGACY_CONVENTIONS_DOC_ID:
        return True
    if not doc_id.startswith(_CONVENTIONS_PREFIX):
        return False
    # 접두만 보면 `graph.md~notes` 같은 남의 문서를 삼킨다 — 꼬리가 해시인지까지 본다.
    return bool(_OWNER_TAG_RE.match(doc_id[len(_CONVENTIONS_PREFIX):]))

#: 문서 미러 개념의 id 접두. 문서 `<doc_id>` 의 미러는 `concept:doc.<doc_id>` 다.
#: 미러 id 를 만들고 푸는 일은 아래 두 함수가 하고, 접두만 보는 곳은 이 상수를 쓴다.
DOC_MIRROR_PREFIX = "concept:doc."

def mirror_id(doc_id: str) -> str:
    """문서 id → 그 문서 미러 개념의 논리 id(`pkns.key` 로 소유자를 입히기 전의 값)."""
    return DOC_MIRROR_PREFIX + doc_id

def doc_id_of(node_id: str) -> str:
    """미러 개념의 논리 id → 문서 id. 접두 길이만큼 앞을 잘라 낼 뿐 접두를 **확인하지
    않는다** — 접두가 없는 id 에는 앞 글자를 잃은 꼬리가 나온다. 부르는 쪽이 먼저
    `node_id.startswith(DOC_MIRROR_PREFIX)` 로 가른다."""
    return node_id[len(DOC_MIRROR_PREFIX):]

def is_conventions_chunk_id(chunk_id: str) -> bool:
    """관례 문서의 청크인가(`docstore.chunk_id_of` 규약: ``{doc_id}#{index:05d}``).

    검색 팔이 쥐고 있는 것은 chunk_id 뿐이라 추가 질의 없이 여기서 가른다."""
    return is_conventions_doc_id(str(chunk_id or "").rsplit("#", 1)[0])
