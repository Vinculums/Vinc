# -*- coding: utf-8 -*-
"""병합 후보와 기각 기억: 제목이 닮은 개념 쌍을 제안만 하는 결정론 읽기와, "이 둘은 다른 것"을 적어 두는 표.

`suggest_merge_candidates`(`vinc_merge_candidates`)가 소유자의 개념 제목을 읽어 `merge_candidates_detail` 로 쌍을
매기고, 문서 미러가 낀 쌍(`_is_structural_nonduplicate`)과 기각한 쌍(`_merge_rejection_rows`)을 빼며 뺀 수를
`suppressed` 로 돌려준다. 병합 확정은 여기 없다: `graph_edit` 의 `merge_nodes` 를 거친다.
`reject_merge_pair`(`vinc_merge_reject`)가 `_MergeRejection` 메타 표에 기각을 적거나 지우고,
`list_merge_rejections`(`vinc_merge_rejections`)가 그 목록을 읽는다. 표의 키는 `rejection_pair_key` 가 짓고,
소유자를 접는 `api/owner_fold.py` 도 같은 함수로 키를 다시 짓는다.

`api/query.py` 가 여기 이름을 모두 다시 내보내므로 `q.suggest_merge_candidates`·`q.X` 와 `from api.query import X`
는 그대로다. 두 이름은 같은 객체다. 여기 함수가 부르는 이름(`execute`·`open_store`·`_merge_rejection_rows` 등)은
이 모듈에서 찾으므로, 그 호출을 바꾸려면 `api.query` 가 아니라 이 모듈의 이름을 패치한다.

스토어를 안 받으면 `_store_scope`(`api/query_base.py`)가 열고 닫는다. 열 수 없을 때의 규칙: 읽기
(`suggest_merge_candidates`·`list_merge_rejections`)는 `executed:False` 에 `note` 만 싣고, 쓰기(`reject_merge_pair`)는
`note` 와 `error` 를 같이 싣는다.

이 모듈은 `api.query` 를 import 하지 않는다(`tests/core/test_merge_candidates_module.py` 가 본다).
"""
from __future__ import annotations

import re

from store.backend import open_store
from store.querybase import DOC_MIRROR_PREFIX, QueryPlan, execute

from .query_base import _store_scope


# ── ER 병합 후보 제안 (E-G4) — 조회 전용, 자동 병합 금지(THESIS T1) ────────────
# 리서치 ③(개체 식별 해소)의 **검출 단계만** 채택한다 — 자동 병합은 영구 금지
# (오병합 비가역·LLM 판정 반입 금지·"시스템은 제안만"). 여기서는 결정론 타이틀
# 유사로 중복 후보를 **제안**하고, 확정(엣지 재지향+삭제)은 기존 merge_nodes
# (비가역·사용자 지시·단일 쓰기 표면 R4) 경유. suggest_ties(D25 전 단계)와 동형의
# 조회-전용 계층. 실사용 사고(중복 개념 오염 concept:ipaas)의 예방 표면.
_MERGE_TOKEN_RE = re.compile(r"[0-9a-z가-힣]+")


def _norm_title(title: str) -> tuple[str, frozenset]:
    """타이틀 정규화 → (정규 문자열, 토큰셋). 소문자·영숫자/한글 토큰. 결정론.

    ⚠ 여기 **길이≥2 토큰만 유의어로 치는 필터가 있었고, 그게 F10 오탐의 최대 원인**
    이었다(실측). 짧은 토큰이 곧 잡음이라는 가정이 거꾸로였다:

      · "웹 Vinc" → `웹` 이 버려져 토큰셋이 `{vinc}` 하나가 된다. 그러면 vinc 를
        포함한 **모든** 제목의 부분집합이라 subset 규칙이 전부 후보로 올린다.
        22개념 표본에서 이 노드 하나가 후보쌍 20개 중 10개(50%)를 만들었고,
        라이브 계정 그래프(개념 185)에서도 상위 30쌍 중 12건(40%)이 이 쌍이었다.
      · "SEC-1"/"SEC-3" → 변별점인 `1`·`3` 이 버려져 두 결정이 같은 토큰이 된다.

    즉 필터가 **일반어(vinc)는 남기고 변별어(웹·1·3)를 버리고** 있었다. 토큰을 전부
    유의어로 두면 표본 후보쌍이 37 → 14(−62%)로 줄고, 진짜 양성 4종(iPaaS/ipaas ·
    transformer ⊂ transformer architecture · chunk-ssot ⊂ D45 · LadybugDB ⊂ D14)은
    그대로 남는다. 토큰 추가는 부분집합 관계를 **좁히기만** 하므로(더 보수적) 새
    오탐을 만들지 않는다."""
    toks = [t for t in _MERGE_TOKEN_RE.findall((title or "").lower())]
    return ("".join(toks), frozenset(toks))


# ── 구조적 비중복 쌍 (F10-a) ──────────────────────────────────────────────────
# **한쪽이라도 doc 미러면 후보에서 뺀다.** doc 미러와 그것이 비추는 주제 개념은 사본
# 둘이 아니라 **층 둘**이고, 제목이 같은 것은 미러가 문서 제목을 물려받기 때문이다.
# 이건 id 명명 관례지 엔진 구조가 아니라, 관례가 없는 그래프에서는 자동 무동작이고
# 끄는 스위치(`include_structural`)를 둔다. **무음 억제 금지** — 몇 쌍을 뺐는지 숫자로
# 남긴다. 근거·실측: [[record:fact-doc-concept-is-a-layer]] ·
# [[record:fact-merge-excludes-any-doc-pair]].
# ⚠ 결정용 `dec-*` 접두 규칙을 되살리지 말 것 — 결정은 이제 별도 라벨이고 이 조회는
# `MATCH (c:Concept)` 이라 애초에 안 긁어온다(타입이 생기면 관례는 물러난다).
def _structural_role(cid: str) -> str:
    """개념 id 의 구조 역할 — ``"doc"`` | ``""``(보통)."""
    return "doc" if cid.startswith(DOC_MIRROR_PREFIX) else ""


def _is_structural_nonduplicate(a_id: str, b_id: str) -> bool:
    """이 쌍이 **의도된 비중복**인가 — 한쪽이라도 문서 미러 개념이면 그렇다.

    doc↔doc(문서가 둘이면 개념도 둘)과 doc↔주제(층이 둘)를 같은 규칙으로 뺀다.
    근거는 위 주석 — 두 경우 모두 제목이 닮은 것이 명명 규칙의 결과다."""
    return "doc" in (_structural_role(a_id), _structural_role(b_id))


def rejection_pair_key(owner: str, a_id: str, b_id: str) -> str:
    """기각 기억의 키 — ``owner|min|max``. 정렬해 (a,b)·(b,a) 를 한 행으로 접는다."""
    lo, hi = sorted((a_id or "", b_id or ""))
    return f"{owner or ''}|{lo}|{hi}"


def _rejected_set(pairs) -> set[tuple[str, str]]:
    """기각쌍 → 정렬된 튜플 집합(순서 무관 조회용)."""
    out: set[tuple[str, str]] = set()
    for p in pairs or ():
        if isinstance(p, dict):
            a, b = p.get("a_id") or "", p.get("b_id") or ""
        else:
            a, b = (list(p) + ["", ""])[:2]
        if a and b:
            lo, hi = sorted((a, b))
            out.add((lo, hi))
    return out


def merge_candidates_detail(rows: list[dict], *, min_score: float = 0.6,
                            limit: int = 20, include_structural: bool = False,
                            rejected=None) -> dict:
    """:func:`merge_candidates` 의 상세형 — 억제 내역까지 돌려준다(순수, I/O 없음).

    반환 ``{"candidates": […], "suppressed": {"structural": n, "rejected": m}}``.
    억제를 **숫자로** 남기는 이유: 기본 필터가 진짜 중복을 먹었을 때 호출자가 그
    사실을 알 방법이 없으면 그건 무음 고장이다. 숫자가 0 이 아니면 호출자는
    ``include_structural`` 로 되열어 확인할 수 있다."""
    prepped = []
    for r in rows:
        cid = r.get("id")
        if not cid:
            continue
        norm, toks = _norm_title(r.get("title") or "")
        if not toks:
            continue
        prepped.append({"id": cid, "title": r.get("title") or cid,
                        "domain": r.get("domain") or "", "norm": norm, "toks": toks})
    prepped.sort(key=lambda x: x["id"])   # 결정론 기준 순서
    rej = _rejected_set(rejected)
    out = []
    n_struct = n_rej = 0
    for i in range(len(prepped)):
        a = prepped[i]
        for j in range(i + 1, len(prepped)):
            b = prepped[j]
            score = 0.0
            reason = None
            if a["norm"] and a["norm"] == b["norm"]:
                score, reason = 1.0, "exact_title"
            else:
                inter = len(a["toks"] & b["toks"])
                union = len(a["toks"] | b["toks"]) or 1
                jac = inter / union
                if a["toks"] <= b["toks"] or b["toks"] <= a["toks"]:
                    # ⚠ 여기가 `max(0.8, jac)` 였다. 부분집합이면 겹침 정도와 무관하게
                    # 0.8 바닥이 깔려서, 실측 20쌍 중 19쌍이 **같은 점수 0.8** 이었다.
                    # 점수가 한 값이면 정렬은 id 사전순과 다를 게 없고 호출자는 무엇을
                    # 먼저 볼지 고를 수 없다. 겹침에 단조인 [0.8,1.0] 로 편다.
                    score, reason = 0.8 + 0.2 * jac, "subset"
                elif jac >= min_score:
                    score, reason = jac, "token_overlap"
            if not reason:
                continue
            if not include_structural and _is_structural_nonduplicate(a["id"], b["id"]):
                n_struct += 1
                continue
            if (a["id"], b["id"]) in rej:   # prepped 가 id 정렬이라 a<b 보장
                n_rej += 1
                continue
            out.append({"a_id": a["id"], "a_title": a["title"],
                        "b_id": b["id"], "b_title": b["title"],
                        "score": round(score, 4), "reason": reason,
                        "same_domain": bool(a["domain"] and a["domain"] == b["domain"])})
    out.sort(key=lambda c: (not c["same_domain"], -c["score"], c["a_id"], c["b_id"]))
    return {"candidates": out[:max(1, int(limit))],
            "suppressed": {"structural": n_struct, "rejected": n_rej},
            "total": len(out)}


def merge_candidates(rows: list[dict], *, min_score: float = 0.6,
                     limit: int = 20, include_structural: bool = False,
                     rejected=None) -> list[dict]:
    """중복 개념 후보쌍을 결정론적으로 산출(순수 — I/O 없음). ``rows``: [{id, title,
    domain}]. 규칙(강→약): 정규 타이틀 완전일치(exact_title, 1.0) · 부분집합
    (subset, 0.8+0.2·자카드) · 토큰 자카드 ≥min_score(token_overlap). 반환은
    (동일도메인 우선, 점수 내림차순, id 사전순) 정렬 리스트.

    기본으로 **의도된 비중복 쌍**(한쪽이라도 `concept:doc.*` — 문서끼리 · 문서↔주제,
    :func:`_is_structural_nonduplicate`)과 **기각 기억**(``rejected``)을 뺀다.
    ``include_structural=True`` 로 구조 필터를 끄면 예전 동작이다. 억제 건수가
    필요하면 :func:`merge_candidates_detail`."""
    return merge_candidates_detail(rows, min_score=min_score, limit=limit,
                                   include_structural=include_structural,
                                   rejected=rejected)["candidates"]


# ── 기각 기억 (F10-b) — "이 둘은 다른 것" 판정을 적어 둔다 ────────────────────
# merge_candidates 는 결정론이라 같은 데이터에 늘 같은 답을 낸다. 그래서 사용자가
# 오탐 하나를 기각해도 다음 호출에 그 쌍이 또 1등으로 올라온다 — 목록이 영원히 같은
# 잡음으로 채워진다. 판정을 저장해 그 쌍만 뺀다. **읽기 도구의 상태를 사용자가 고칠
# 수 있게** 하는 최소 장치이고, 병합(비가역) 대신 기각(가역)을 권하는 안전판이기도 하다.
#
# 저장 위치는 `_MergeRejection` 메타 테이블([[vinc.spec.dataplane §2.5]]).
# owner 스코프는 **키와 열 양쪽**에 건다 — SEC-1 이 준 교훈은 한 층만 맞으면 새는
# 것이었다([[vinc.spec.dataplane §5]]).

def _merge_rejection_rows(store, owner: str | None) -> list[dict]:
    """owner 의 기각 목록(원시 행). 테이블이 없는 구 스토어면 빈 목록으로 강등한다 —
    기각 기억이 없다고 후보 조회 자체가 죽으면 안 된다."""
    if store is None:
        return []
    try:
        return store.run(
            "MATCH (r:_MergeRejection) WHERE r.owner_id = $o "
            "RETURN r.a_id AS a_id, r.b_id AS b_id, r.rejected_at AS rejected_at, "
            "r.note AS note ORDER BY a_id, b_id", {"o": owner or ""})
    except Exception:
        return []


def list_merge_rejections(*, owner: str | None = None, cfg=None, store=None) -> dict:
    """기각된 병합 후보쌍 목록(조회 전용). 반환 {executed, rejections:[…], count}."""
    with _store_scope(store, cfg, open_store) as store:
        if store is None:
            return {"kind": "merge_rejections", "executed": False, "rejections": [],
                    "count": 0, "note": "no reachable store"}
        rows = _merge_rejection_rows(store, owner)
        return {"kind": "merge_rejections", "executed": True,
                "rejections": [dict(r) for r in rows], "count": len(rows)}


def reject_merge_pair(*, owner: str, a_id: str, b_id: str, note: str = "",
                      undo: bool = False, cfg=None, store=None) -> dict:
    """병합 후보쌍을 기각(또는 ``undo=True`` 로 기각 취소). 멱등.

    owner 는 **필수**다. 없이 쓰면 남의 그래프 쌍을 기각해 상대의 제안 목록을 조용히
    비울 수 있다(SEC-3 이 쓰기 표면에서 잡았던 것과 같은 구멍)."""
    base = {"kind": "merge_reject", "executed": False,
            "a_id": a_id, "b_id": b_id, "undo": bool(undo)}
    if not owner or not isinstance(owner, str):
        base["error"] = base["note"] = "refusing to record a rejection without an owner"
        return base
    if not a_id or not b_id or a_id == b_id:
        base["error"] = base["note"] = "two distinct node ids are required"
        return base
    with _store_scope(store, cfg, open_store) as store:
        try:
            if store is None:
                base["error"] = base["note"] = "no reachable store"
                return base
            key = rejection_pair_key(owner, a_id, b_id)
            lo, hi = sorted((a_id, b_id))
            if undo:
                store.run("MATCH (r:_MergeRejection) WHERE r.pair_key = $k DELETE r", {"k": key})
            else:
                import datetime as _dt
                ts = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds")
                store.run(
                    "MERGE (r:_MergeRejection {pair_key: $k}) SET r.owner_id = $o, "
                    "r.a_id = $a, r.b_id = $b, r.rejected_at = $ts, r.note = $n",
                    {"k": key, "o": owner, "a": lo, "b": hi, "ts": ts, "n": str(note or "")})
            base["executed"] = True
            base["pair_key"] = key
            base["count"] = len(_merge_rejection_rows(store, owner))
        except Exception as exc:
            base["error"] = base["note"] = f"{type(exc).__name__}: {exc}"
    return base


def suggest_merge_candidates(*, owner: str | None = None, min_score: float = 0.6,
                             limit: int = 20, include_structural: bool = False,
                             cfg=None, store=None) -> dict:
    """E-G4 — owner 개념의 중복 병합 후보(조회 전용). 확정은 vinc_merge_nodes 경유
    (자동 병합 금지 THESIS T1).

    기본으로 **의도된 비중복 쌍**(한쪽이라도 문서 개념 — 문서끼리 · 문서↔주제)과
    **기각 기억**을 뺀다(F10). 뺀 건수는 ``suppressed`` 로 노출한다 — 필터가 진짜
    중복을 먹었는지 호출자가 볼 수 있어야 하고, ``include_structural=True`` 로 구조
    필터를 되열 수 있다.

    반환 {executed, candidates:[…], count, total, suppressed:{structural, rejected}}."""
    where = "WHERE c.owner_id = $owner " if owner else ""
    params = {"owner": owner} if owner else None
    plan = QueryPlan("merge_candidates",
                     "MATCH (c:Concept) " + where +
                     "RETURN c.concept_id AS id, c.title AS title, c.domain AS domain",
                     params or {}, note="concept dup candidates (read-only)")
    # ⚠ 스토어를 **여기서** 소유한다. `execute(store=None)` 은 자기 커넥션을 열고
    # 닫으므로, 기각 조회에 넘길 store 가 None 으로 남아 필터가 조용히 무동작이 된다.
    # 두 조회가 같은 커넥션을 봐야 한다.
    with _store_scope(store, cfg, open_store) as store:
        res = execute(plan, cfg, store=store)
        if not res.get("executed"):
            return {"kind": "merge_candidates", "executed": False,
                    "candidates": [], "count": 0, "total": 0,
                    "suppressed": {"structural": 0, "rejected": 0},
                    "note": res.get("note", "")}
        det = merge_candidates_detail(
            res.get("rows", []), min_score=min_score, limit=limit,
            include_structural=include_structural,
            rejected=_merge_rejection_rows(store, owner))
    return {"kind": "merge_candidates", "executed": True,
            "candidates": det["candidates"], "count": len(det["candidates"]),
            "total": det["total"], "suppressed": det["suppressed"]}
