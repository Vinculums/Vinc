# -*- coding: utf-8 -*-
"""에이전트 쓰기 검토(A5) — 최근 쓰기를 출처별로 묶고, 사람이 볼 것만 표시한다. 조회 전용.

스토어는 **누가 썼는지**(클라이언트·실행)를 노드에 적지 않는다. 그래서 출처는 스토어에 실제로
남은 것에서만 읽는다: 관계의 근거가 실행 id 하나면 그 실행이 출처이고, 그 관계의 끝점도 같은
출처로 묶는다. 그 밖은 "출처 기록 없음"이다. 추측해서 묶지 않는다.

사람이 볼 것(검사):
  - ``evidence_is_an_id``: 근거가 문장이 아니라 띄어쓰기 없는 id 하나다(실행 id 등).
  - ``evidence_missing``: 근거가 필요한 관계(about · 기록에 닿는 관계)에 근거가 없다.
  - ``decision_without_start``: 결정에 효력 시작일(props.valid_from)이 없다.
  - ``props_shredded``: 속성 값이 낱글자 배열로 저장됐다(문자열이 글자마다 쪼개진 쓰기).
검사가 하나도 없는 묶음은 화면이 "모두 유지"로 한 번에 넘긴다.
근거: [[vinc.research.graph-pages-and-marks-2026-10-25]] §2 · §5
"""
from __future__ import annotations

import datetime
import re

from store import pkns
from store.embedded_schema import EDGE_BIRTH_RELS, EDGE_EVIDENCE_RELS

from . import events, query as q

DEFAULT_DAYS = 14
MAX_DAYS = 60
#: 한 번에 내는 항목 상한(노드 + 관계). 넘으면 `truncated` 로 밝힌다.
MAX_ITEMS = 500
#: 출처가 없는 묶음의 열쇠.
NO_SOURCE = "none"
#: 최근 쓰기를 훑는 노드 표 — (라벨, PK). 실행 기록 Episode 는 출처라 항목에서 뺀다.
_NODE_TABLES = (("Concept", "concept_id"), ("Record", "record_id"), ("Decision", "decision_id"),
                ("Episode", "episode_id"))
_RUN_TOKEN = re.compile(r"^(?:episode:run-|run[:_-])\S+$", re.I)
#: 낱글자 배열로 볼 길이 — 짧은 목록(["a","b"])은 정상 값일 수 있다.
_SHRED_MIN = 8


def clamp_days(value) -> int:
    if value in (None, ""):
        return DEFAULT_DAYS
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise ValueError("invalid_days: expected an integer") from None
    if not 1 <= n <= MAX_DAYS:
        raise ValueError(f"invalid_days: expected 1..{MAX_DAYS}")
    return n


def _since(days: int, now: datetime.datetime | None = None) -> str:
    at = (now or datetime.datetime.now(datetime.timezone.utc)) - datetime.timedelta(days=days)
    return at.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def shredded(props: dict) -> bool:
    """속성 값 하나라도 낱글자 배열인가(270 글자로 쪼개진 문자열 같은 것)."""
    for v in props.values():
        if isinstance(v, list) and len(v) >= _SHRED_MIN and all(isinstance(x, str) and len(x) == 1 for x in v):
            return True
    return False


def evidence_check(rel: str, ev: str, needs: bool) -> str | None:
    text = (ev or "").strip()
    if not text:
        return "evidence_missing" if needs else None
    return "evidence_is_an_id" if not re.search(r"\s", text) else None


def _needs_evidence(rel: str, src: str, dst: str) -> bool:
    # GRAPH.md: about 과 기록에 닿는 관계는 근거를 싣는다.
    return rel == "ABOUT" or "Record" in (src, dst)


def scan(store, *, owner: str, days: int = DEFAULT_DAYS, now: datetime.datetime | None = None) -> dict:
    base = {"kind": "agent_writes", "executed": False}
    if not owner:
        return {**base, "error": "owner is required"}
    since = _since(days, now)
    try:
        nodes = _recent_nodes(store, owner, since)
        rels = _recent_relations(store, owner, since)
    except events._StoreFailed as exc:
        return {**base, "error": str(exc)}
    # 출처: 근거가 실행 id 인 관계의 실행. 그 끝점도 같은 출처로 묶는다(먼저 본 것이 이긴다).
    source_of: dict[str, str] = {}
    items: list[dict] = []
    for r in rels:
        token = (r["evidence"] or "").strip()
        src = token if _RUN_TOKEN.match(token) else NO_SOURCE
        check = evidence_check(r["type"], r["evidence"], r["needs"])
        items.append({"kind": "relation", "id": f'{r["from"]}|{r["type"]}|{r["to"]}', "type": r["type"],
                      "from": r["from"], "to": r["to"], "title": r["to_title"] or r["to"],
                      "at": r["at"], "checks": [check] if check else [], "source": src})
        if src != NO_SOURCE:
            for end in (r["from"], r["to"]):
                source_of.setdefault(end, src)
    for n in nodes:
        checks = []
        if n["kind"] == "decision" and not str(n["props"].get("valid_from") or "").strip():
            checks.append("decision_without_start")
        if shredded(n["props"]):
            checks.append("props_shredded")
        items.append({"kind": "node", "id": n["id"], "node_kind": n["kind"], "title": n["title"] or n["id"],
                      "at": n["at"], "checks": checks, "source": source_of.get(n["id"], NO_SOURCE)})
    items.sort(key=lambda i: (i["at"], i["id"]), reverse=True)
    truncated = len(items) > MAX_ITEMS
    items = items[:MAX_ITEMS]
    groups: dict[str, dict] = {}
    for it in items:
        g = groups.setdefault(it["source"], {"source": {"key": it["source"],
            "kind": "none" if it["source"] == NO_SOURCE else "run"}, "items": [], "open": 0})
        g["items"].append({k: v for k, v in it.items() if k != "source"})
        g["open"] += 1 if it["checks"] else 0
    ordered = sorted(groups.values(), key=lambda g: (g["source"]["kind"] == "none", -len(g["items"]),
                                                     g["source"]["key"]))
    return {"kind": "agent_writes", "executed": True, "since": since, "days": days,
            "groups": ordered, "total": len(items), "truncated": truncated}


def _recent_nodes(store, owner: str, since: str) -> list[dict]:
    out: list[dict] = []
    for label, pk in _NODE_TABLES:
        rows = events._run(q.QueryPlan(
            f"agent_writes_{label}",
            f"MATCH (n:{label}) WHERE n.owner_id = $owner AND n.updated_at >= $since "
            f"RETURN n.{pk} AS id, n.title AS title, n.props AS props, n.updated_at AS at",
            {"owner": owner, "since": since}), store)
        for row in rows:
            nid = pkns.strip(row["id"])
            if nid.startswith(events.RUN_PREFIX):
                continue
            out.append({"id": nid, "kind": label.lower(), "title": events._title(row.get("title")),
                        "props": events._props(row.get("props")), "at": str(row.get("at") or "")})
    return out


def _recent_relations(store, owner: str, since: str) -> list[dict]:
    out: list[dict] = []
    for rel, pairs in q.SYNC_EDGE_RELS.items():
        if rel not in EDGE_BIRTH_RELS:
            continue
        has_ev = rel in EDGE_EVIDENCE_RELS
        for src, spk, dst, dpk in pairs:
            ev = "r.evidence" if has_ev else "''"
            rows = events._run(q.QueryPlan(
                f"agent_writes_{rel}_{src}_{dst}",
                f"MATCH (a:{src})-[r:{rel}]->(b:{dst}) WHERE a.owner_id = $owner AND r.created >= $since "
                f"RETURN a.{spk} AS s, b.{dpk} AS t, b.title AS tt, {ev} AS ev, r.created AS at",
                {"owner": owner, "since": since}), store)
            for row in rows:
                out.append({"type": rel, "from": pkns.strip(row["s"]), "to": pkns.strip(row["t"]),
                            "to_title": events._title(row.get("tt")), "evidence": str(row.get("ev") or ""),
                            "at": str(row.get("at") or ""), "needs": has_ev and _needs_evidence(rel, src, dst)})
    return out
