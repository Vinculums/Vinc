"""시점 읽기 후처리: `as_of`(그때 성립하던 것)와 `known_at`(그때 그래프가 알던 것).

두 축은 다르다. `as_of` 는 **유효 시간**이고 노드 자신의 구간(Record 열, Decision props,
Episode 의 날짜)을 본다. `known_at` 은 **그래프 시간**이고 노드가 저장된 시각(`created`)을
본다. 둘 다 기간(`YYYY`, `YYYY-MM`, `YYYY-MM-DD`)이고 기본값(둘 다 없음)에서는 결과를 한
바이트도 바꾸지 않는다.

질의 함수는 건드리지 않는다. 이미 나온 결과에서 노드를 걸러 내고, 몇 개를 왜 감췄는지
응답에 싣는다. 시드는 감추지 않는다(무엇을 물었는지는 남아야 한다). 엣지는 양 끝이 남을
때만 남는다(구간은 끝점에서 파생한다).

없는 시각은 지어내지 않는다. `created` 가 빈 노드(Episode 는 열 자체가 없다)는 `known_at`
에서 감추지 않고 `known_at_unknown` 으로 센다. Concept 은 유효구간이 없으므로 `as_of` 에서
늘 남는다.

search·brief 는 이 후처리를 타지 않는다. 둘 다 시간 인자를 자기 경로에서 적용하고,
brief 는 관계를 거를 때 이 모듈의 `Gate` 를 그대로 쓴다(같은 요청이면 같은 결과).

근거: [[vinc.plan.temporal-edges-point-in-time-document-validity-2026-09-28]] §1
"""
from __future__ import annotations

from store import pkns
from store.querybase import _props_to_obj, holds_during, time_scope

#: 접두 → (라벨, PK, 구간을 어디서 읽나, 생성 열이 있나)
_KINDS = {
    "record": ("Record", "record_id", "columns", True),
    "decision": ("Decision", "decision_id", "props", True),
    "concept": ("Concept", "concept_id", None, True),
    "bridge": ("Bridge", "bridge_id", None, True),
    "episode": ("Episode", "episode_id", "date", False),
}


def parse(as_of: str | None, known_at: str | None):
    """→ (as_of 기간 또는 None, known_at 기간 또는 None, 응답에 실을 설명 dict).
    틀린 값은 ValueError — 조용히 오늘로 떨어지면 호출부는 과거를 읽은 줄 안다."""
    info: dict = {}
    scopes = []
    for name, value in (("as_of", as_of), ("known_at", known_at)):
        v = (value or "").strip()
        if not v:
            scopes.append(None)
            continue
        scope = time_scope(v)
        if scope is None:
            raise ValueError(f"invalid {name} {value!r}: use YYYY, YYYY-MM or YYYY-MM-DD")
        scopes.append(scope)
        info[name] = {"value": v, "from": scope[0], "until": scope[1]}
    return scopes[0], scopes[1], info


def node_times(store, ids, *, owner: str | None) -> dict[str, dict]:
    """id → {valid_from, valid_until, created, date}. 모르는 접두·없는 노드는 빠진다."""
    by_kind: dict[str, list[str]] = {}
    for nid in {i for i in ids if isinstance(i, str)}:
        kind = nid.split(":", 1)[0]
        if kind in _KINDS:
            by_kind.setdefault(kind, []).append(nid)
    out: dict[str, dict] = {}
    for kind, nids in by_kind.items():
        label, pk, interval, has_created = _KINDS[kind]
        cols = [f"n.{pk} AS id"]
        if interval == "columns":
            cols += ["n.valid_from AS vf", "n.valid_until AS vu"]
        if interval == "props":
            cols.append("n.props AS props")
        if interval == "date":
            cols.append("n.date AS date")
        if has_created:
            cols.append("n.created AS created")
        scope = " AND n.owner_id = $owner" if owner else ""
        rows = store.run(f"MATCH (n:{label}) WHERE n.{pk} IN $ids{scope} "
                         f"RETURN {', '.join(cols)}",
                         {"ids": pkns.keys(owner, sorted(nids)), "owner": owner or ""})
        for r in rows:
            vf, vu = str(r.get("vf") or ""), str(r.get("vu") or "")
            if interval == "props":
                props = _props_to_obj(r.get("props"))
                vf, vu = str(props.get("valid_from") or ""), str(props.get("valid_until") or "")
            out[pkns.strip(r.get("id") or "")] = {
                "valid_from": vf, "valid_until": vu,
                "created": str(r.get("created") or ""), "date": str(r.get("date") or ""),
                "has_created": has_created}
    return out


class Gate:
    """한 응답 안에서 같은 판정을 여러 번 쓰려고 묶었다. ``keep(id)`` 가 핵심이다."""

    def __init__(self, store, ids, *, owner, as_of_scope, known_scope):
        self.as_of, self.known = as_of_scope, known_scope
        self.times = node_times(store, ids, owner=owner)
        self.hidden_validity = self.hidden_known = 0
        self.unknown_known: set[str] = set()

    def _holds(self, t: dict) -> bool:
        if self.as_of is None:
            return True
        if t["date"]:                       # 사건은 그 날짜에 일어났다. 끝나지 않는다
            return holds_during(t["date"], "", self.as_of)
        return holds_during(t["valid_from"], t["valid_until"], self.as_of)

    def _known(self, nid: str, t: dict) -> bool:
        if self.known is None:
            return True
        created = t["created"][:10]
        if not created:
            self.unknown_known.add(nid)     # 모른다. 감추지 않는다
            return True
        return created < self.known[1]

    def keep(self, nid: str) -> bool:
        t = self.times.get(nid)
        if t is None:                       # 도메인 좌표·모르는 접두는 판정 밖이다
            return True
        if not self._holds(t):
            self.hidden_validity += 1
            return False
        if not self._known(nid, t):
            self.hidden_known += 1
            return False
        return True

    def stamp(self, res: dict, info: dict) -> dict:
        res["time_scope"] = {**res.get("time_scope", {}), **info} if isinstance(
            res.get("time_scope"), dict) else info
        if self.as_of is not None:
            res["validity_filtered"] = int(res.get("validity_filtered") or 0) + self.hidden_validity
        if self.known is not None:
            res["known_at_filtered"] = self.hidden_known
            res["known_at_unknown"] = len(self.unknown_known)
            if self.unknown_known:
                res["known_at_note"] = ("nodes with no stored creation time were kept and "
                                        "counted in known_at_unknown; their recording time "
                                        "is not known")
        return res


#: 이 후처리가 다루는 결과. search·brief 는 시간 인자를 자기 경로에서 전부 적용하므로
#: 여기 없다(감사 B1). 여기서 다시 거르면 해소가 끝난 뒤에 거르는 셈이 된다.
KINDS = ("neighbors", "graph", "path", "recall")


def apply(kind: str, res, *, store, owner, as_of=None, known_at=None, seed=None):
    """``kind`` 별 결과 모양에 맞춰 거른다. 둘 다 없으면 결과를 그대로 돌려준다."""
    if kind not in KINDS:
        raise ValueError(f"temporal_read does not filter {kind!r} results")
    if not isinstance(res, dict) or not ((as_of or "").strip() or (known_at or "").strip()):
        return res
    try:
        a, k, info = parse(as_of, known_at)
    except ValueError as exc:
        return {"executed": False, "error": f"invalid_time_scope: {exc}", "note": str(exc)}
    if not res.get("executed", True):
        return res
    ids: list[str] = []
    if kind == "neighbors":
        ids = [n.get("id") for n in res.get("neighbors") or []]
    elif kind == "graph":
        ids = [n.get("id") for n in res.get("nodes") or []]
    elif kind == "path":
        ids = [i for p in res.get("paths") or [] for i in p.get("ids") or []]
    elif kind == "recall":
        ids = [r.get("id") for r in res.get("rows") or []]
    gate = Gate(store, [i for i in ids if i and i != seed], owner=owner,
                as_of_scope=a, known_scope=k)
    keep = gate.keep
    if kind == "neighbors":
        kept = [n for n in res.get("neighbors") or [] if keep(n.get("id"))]
        by_rel: dict[str, int] = {}
        for n in kept:
            by_rel[n.get("rel")] = by_rel.get(n.get("rel"), 0) + 1
        res.update(neighbors=kept, by_rel=by_rel, count=len(kept))
    elif kind == "graph":
        nodes = [n for n in res.get("nodes") or [] if n.get("id") == seed or keep(n.get("id"))]
        alive = {n.get("id") for n in nodes}
        res["nodes"] = nodes
        res["edges"] = [e for e in res.get("edges") or []
                        if e.get("source") in alive and e.get("target") in alive]
    elif kind == "path":
        paths = [p for p in res.get("paths") or [] if all(keep(i) for i in p.get("ids") or [])]
        res["paths"], res["count"] = paths, len(paths)
        res["found"] = bool(paths)
    elif kind == "recall":
        res["rows"] = [r for r in res.get("rows") or [] if keep(r.get("id"))]
    return gate.stamp(res, info)
