"""Search time metadata and explicit modes, without changing bodies or stored intervals."""
from __future__ import annotations

import datetime as dt
import json
import re

from store import pkns
from store.querybase import (
    DOC_MIRROR_PREFIX,
    _props_to_obj,
    doc_id_of,
    holds_during,
    mirror_id,
    resolve_as_of,
    time_scope,
)

FIELDS = ("created_at", "updated_at", "content_time", "valid_from", "valid_until",
          "superseded_by")
KINDS = {"concept": ("Concept", "concept_id"), "record": ("Record", "record_id"),
         "decision": ("Decision", "decision_id"), "episode": ("Episode", "episode_id")}

#: 대체 계보를 따라 읽는 노드 수의 상한. 후보 수가 아니라 SUPERSEDES 로 이어진 노드를 센다.
#: 후보 수로 세면 current 모드(후보 최대 2000건)에서 계보가 짧아도 모든 hit 가 판정을 포기한다.
LINEAGE_CAP = 256

#: 날짜만 적힌 시작은 시간대가 없다. 그 날이 가장 먼저 시작되는 곳(UTC+14)에서 시작된 것으로 읽는다.
#: UTC 자정으로 읽으면 KST 로 적은 "오늘 시작"이 매일 아홉 시간 동안 future 로 빠진다.
#: 기본 경로(`querybase.validity_state`)가 future 를 감추지 않는 것과 같은 이유다.
_EARLIEST_ZONE = dt.timedelta(hours=14)
_DATE_ONLY = re.compile(r"\d{4}(?:-\d{2}){0,2}")


def instant(value):
    """Expand partial dates to period starts; compare timestamp offsets in UTC."""
    if not value:
        return None
    value = str(value).strip()
    if re.fullmatch(r"\d{4}(?:-\d{2}){0,2}", value):
        scope = time_scope(value)
        if scope:
            value = scope[0]
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=dt.timezone.utc) if parsed.tzinfo is None else parsed.astimezone(dt.timezone.utc)
    except ValueError:
        return None


def _start_instant(value):
    """구간 시작의 순간. 날짜만 적혔으면 그 날이 지구 어디에선가 시작되는 첫 순간이다."""
    start = instant(value)
    if start is not None and _DATE_ONLY.fullmatch(str(value).strip()):
        return start - _EARLIEST_ZONE
    return start


def overlaps_scope(valid_from, valid_until, scope):
    """Keep half-open period overlap, comparing timestamp offsets as UTC instants."""
    vf, vu = instant(valid_from), instant(valid_until)
    if (valid_from and vf is None) or (valid_until and vu is None):
        return holds_during(valid_from, valid_until, scope)
    start, end = instant(scope[0]), instant(scope[1])
    if start is None or end is None:
        return holds_during(valid_from, valid_until, scope)
    return bool((vf is None or vf < end) and (vu is None or start < vu)
                and not (vf and vu and vf >= vu))


def metadata(row, *, now=None, scope=None):
    props = _props_to_obj(row.get("props_raw", row.get("props")))
    out = {key: row.get(key) or None for key in FIELDS}
    out["created_at"] = row.get("created_at") or row.get("created") or None
    if row.get("kind", row.get("node_kind")) == "episode":
        out["content_time"] = row.get("content_time") or row.get("date") or None
    if row.get("kind", row.get("node_kind")) == "decision":
        for key in ("valid_from", "valid_until"):
            out[key] = props.get(key) or None
    out["superseded_by"] = out["superseded_by"] or props.get("superseded_by") or None
    vf, vu = instant(out["valid_from"]), instant(out["valid_until"])
    now = instant(now) if now else dt.datetime.now(dt.timezone.utc)
    invalid = any(out[k] and instant(out[k]) is None for k in ("valid_from", "valid_until"))
    invalid = invalid or bool(vf and vu and vf >= vu)
    if invalid:
        state = "unknown"
    elif vu and now >= vu:
        state = "historical"
    elif vf and now < _start_instant(out["valid_from"]):
        state = "future"
    elif not vf and not vu:
        state = "unknown"
    else:
        state = "current"
    out.update(validity=state, interval_known=bool(vf or vu) and not invalid,
               interval_invalid=bool(invalid),
               holds_in_scope=(overlaps_scope(out["valid_from"], out["valid_until"], scope)
                               if scope and not invalid else None),
               supersession=[], supersedes=[], claim_status="unscoped", conflicts=[])
    return out


#: `stamp` 이 행에 평평하게 한 번 더 채우는 시간 칸. 같은 이름이 `temporal{}` 안에도 있다.
FLAT = FIELDS + ("validity",)


def stamp(row, source=None, **kw):
    row["temporal"] = metadata(source if source is not None else row, **kw)
    # Keep legacy detail strings and fill only absent search fields.
    # 공개 검색의 요약 응답은 이 사본을 걷는다(:func:`drop_flat`).
    for key in FIELDS:
        row.setdefault(key, row["temporal"][key])
    row.setdefault("validity", row["temporal"]["validity"])
    return row


def drop_flat(result):
    """검색 응답의 `rows`·`node_hits` 에서 평평한 시간 칸(:data:`FLAT`)을 걷는다.

    `temporal{}` 이 있는 행만 걷으므로 시간 메타의 하나뿐인 사본은 지우지 않는다. 행의 수와
    순서, 다른 키는 그대로다. 결과 dict 를 제자리에서 고치고 돌려준다."""
    for key in ("rows", "node_hits"):
        for row in result.get(key) or []:
            if isinstance(row, dict) and isinstance(row.get("temporal"), dict):
                for field in FLAT:
                    row.pop(field, None)
    return result


def mode_scope(mode, historical, as_of, query):
    if mode not in (None, "general", "current", "historical"):
        raise ValueError("mode must be general, current or historical")
    if mode == "current" and historical:
        raise ValueError("current mode conflicts with historical=true")
    scope, info = resolve_as_of(as_of, query)
    if mode == "current" and as_of:
        raise ValueError("current mode conflicts with as_of; use general or historical")
    if mode == "current":
        today = dt.datetime.now(dt.timezone.utc).date().isoformat()
        scope = time_scope(today)
        info = {"mode": "current", "as_of": today, "from": scope[0], "until": scope[1], "source": "clock"}
    return bool(historical or mode == "historical"), scope, info


def node_sources(store, ids, *, owner=None):
    out = {}
    for kind, (label, pk) in KINDS.items():
        selected = sorted({i for i in ids if i and i.split(":", 1)[0] == kind})
        if not selected:
            continue
        cols = [f"n.{pk} AS id", "n.props AS props_raw", "n.updated_at AS updated_at", "n.owner_id AS owner_id"]
        if kind != "episode":
            cols.append("n.created AS created")
        if kind in ("record", "concept"):
            cols.append("n.content_time AS content_time")
        if kind == "episode":
            cols.append("n.date AS content_time")
        if kind == "record":
            cols += ["n.valid_from AS valid_from", "n.valid_until AS valid_until"]
        own = " AND n.owner_id = $owner" if owner else ""
        rows = store.run(f"MATCH (n:{label}) WHERE n.{pk} IN $ids{own} RETURN " + ", ".join(cols),
                         {"ids": pkns.keys(owner, selected), "owner": owner or ""}) or []
        for row in rows:
            row = dict(row)
            nid = pkns.strip(row.get("id") or "")
            out[nid] = {**row, "kind": kind}
    mirrors = {nid: doc_id_of(nid) for nid in out if nid.startswith(DOC_MIRROR_PREFIX)}
    docs = doc_sources(store, mirrors.values(), owner=owner)
    for nid, doc_id in mirrors.items():
        if doc_id in docs:
            out[nid].update({key: docs[doc_id].get(key) for key in FIELDS})
            out[nid]["doc_id"] = doc_id
    return out


def doc_sources(store, ids, *, owner=None):
    ids = sorted({i for i in ids if i})
    if not ids:
        return {}
    own = " AND d.owner_id = $owner" if owner else ""
    cols = ", ".join(f"d.{k} AS {k}" for k in FIELDS)
    return {r.get("id"): r for r in store.run(
        f"MATCH (d:_DocMeta) WHERE d.doc_id IN $ids{own} "
        f"RETURN d.doc_id AS id, d.title AS t, {cols}", {"ids": ids, "owner": owner or ""}) or []}


def supersession_edges(store, sources, *, owner=None):
    edges = []
    # Require the same owner and kind at both ends; a props pointer is not evidence.
    for kind in ("record", "decision", "concept"):
        label, pk = KINDS[kind]
        ids = [nid for nid, r in sources.items() if r.get("kind") == kind]
        if not ids:
            continue
        own = " AND a.owner_id = $owner AND b.owner_id = $owner" if owner else " AND a.owner_id = b.owner_id"
        for r in store.run(f"MATCH (a:{label})-[e:SUPERSEDES]->(b:{label}) "
                           f"WHERE (a.{pk} IN $ids OR b.{pk} IN $ids){own} "
                           f"RETURN a.{pk} AS new, b.{pk} AS old, e.evidence AS evidence",
                           {"ids": pkns.keys(owner, ids), "owner": owner or ""}) or []:
            new, old = pkns.strip(r.get("new") or ""), pkns.strip(r.get("old") or "")
            if kind == "concept" and not all(i.startswith(DOC_MIRROR_PREFIX) for i in (new, old)):
                continue
            edges.append({"new": new, "old": old, "evidence": str(r.get("evidence") or "").strip()})
    return edges


def claim_key(row):
    p = _props_to_obj(row.get("props_raw", row.get("props")))
    # Similar titles or topics do not establish claim identity; require an explicit scope key.
    if not all(k in p and p[k] is not None for k in ("entity", "predicate", "scope")):
        return None
    if not p["entity"] or not p["predicate"]:
        return None
    return (row.get("owner_id"),) + tuple(json.dumps(p[k], sort_keys=True) for k in ("entity", "predicate", "scope"))


def annotate_claims(hits, sources, edges, *, scope=None):
    by_id = {h["id"]: h for h in hits if h.get("id")}
    adjacency = {}
    for edge in edges:
        if edge["evidence"]:
            adjacency.setdefault(edge["new"], set()).add(edge["old"])

    def reaches(start, target):
        seen, todo = set(), list(adjacency.get(start, ()))
        while todo:
            nid = todo.pop()
            if nid == target:
                return True
            if nid not in seen:
                seen.add(nid)
                todo.extend(adjacency.get(nid, ()))
        return False

    cyclic = {nid for nid in adjacency if reaches(nid, nid)}
    retired = set()
    for edge in edges:
        new, old = edge["new"], edge["old"]
        if new not in by_id or old not in by_id:
            continue
        n, o = by_id[new]["temporal"], by_id[old]["temporal"]
        cycle = new in cyclic or old in cyclic if edge["evidence"] else False
        key = claim_key(sources.get(new, {}))
        comparable = key is not None and key == claim_key(sources.get(old, {}))
        comparable = comparable and n["interval_known"] and o["interval_known"] and (
            _interval(n)[0] < _interval(o)[1] and _interval(o)[0] < _interval(n)[1])
        status = "cycle" if cycle else "unsupported" if not edge["evidence"] else "comparable" if comparable else "uncomparable"
        o["supersession"].append({**edge, "status": status})
        n["supersedes"].append({**edge, "status": status})
        if cycle:
            n["claim_status"] = o["claim_status"] = "cycle"
        elif edge["evidence"] and comparable and (n["holds_in_scope"] if scope else n["validity"] == "current"):
            o["claim_status"] = "superseded"
            retired.add(old)
    groups = {}
    for nid, hit in by_id.items():
        key = claim_key(sources.get(nid, {}))
        if key is not None:
            groups.setdefault((hit.get("kind"), key), []).append(hit)
            if hit["temporal"]["claim_status"] == "unscoped":
                hit["temporal"]["claim_status"] = "date_unknown" if hit["temporal"]["validity"] == "unknown" else "candidate"
    for group in groups.values():
        active = [h for h in group if h["id"] not in retired and h["temporal"]["interval_known"] and not h["temporal"]["interval_invalid"] and (
            h["temporal"]["holds_in_scope"] if scope else h["temporal"]["validity"] == "current")]
        # Parse each value and interval once; this cache lives only inside this call.
        missing = object()
        values = {h["id"]: _props_to_obj(sources.get(h["id"], {}).get("props_raw")).get("value", missing)
                  for h in active}
        intervals = {h["id"]: _interval(h["temporal"]) for h in active}
        for hit in active:
            value = values[hit["id"]]
            if value is missing:
                continue
            for other in active:
                other_value = values[other["id"]]
                if other is hit or other_value is missing or value == other_value:
                    continue
                left, right = intervals[hit["id"]], intervals[other["id"]]
                if left[0] < right[1] and right[0] < left[1]:
                    hit["temporal"]["conflicts"].append(other["id"])
                    if hit["temporal"]["claim_status"] != "cycle":
                        hit["temporal"]["claim_status"] = "conflict"
    return retired


def _interval(t):
    return (instant(t["valid_from"]) or dt.datetime.min.replace(tzinfo=dt.timezone.utc),
            instant(t["valid_until"]) or dt.datetime.max.replace(tzinfo=dt.timezone.utc))


def _demoting(e):
    return bool(e.get("evidence")) and e.get("status") in ("comparable", "uncomparable")


def chain_moved_on(context):
    """대체 계보가 이미 지나간 노드의 id: 구간이 끝났거나, 근거 있는 SUPERSEDES 를 새 쪽으로
    따라가면 구간이 지금인 노드가 있다. `context` 는 :func:`finish_nodes` 가 이미 읽은 계보라
    스토어를 다시 읽지 않는다(크기는 :data:`LINEAGE_CAP` 안팎, 간선마다 한 번 걷는다)."""
    older = {}
    for row in context:
        for e in row["temporal"].get("supersession") or ():
            if _demoting(e):
                older.setdefault(e.get("new"), set()).add(row.get("id"))
    todo = [r.get("id") for r in context if r["temporal"]["validity"] == "current"]
    moved = {r.get("id") for r in context if r["temporal"]["validity"] == "historical"}
    reached = set()
    while todo:
        for old in older.get(todo.pop(), ()):
            if old not in reached:
                reached.add(old)
                todo.append(old)
    return frozenset(moved | reached)


def superseded_old_end(row, current_ids=frozenset(), moved_on=frozenset()):
    """근거 있는 SUPERSEDES 에서 대체된 쪽(비교 가능·불가 모두)을 내릴 것인가. 자기 구간이 오늘
    성립하지 않거나, 그 간선의 새 쪽이 `current_ids`(같이 세우는 행 중 구간이 지금인 id)에 있거나,
    새 쪽마저 계보가 지나갔으면(`moved_on`, :func:`chain_moved_on`) 내린다. 새 쪽이 지금인데 같이
    세우는 행에 없거나 미래라 빠졌으면 오늘 유효한 대체된 쪽은 그대로 둔다.
    순환과 근거 없는 간선은 세지 않는다.
    근거: [[record:on-the-hosted-server-colbert-rescoring-decides-the-final-mode-current-order-and-the-window-ordering-only-picks-the-20-rows-2026-11-11]]"""
    own_current = row["temporal"]["validity"] == "current"
    return any(_demoting(e) and (not own_current or e.get("new") in current_ids or e.get("new") in moved_on)
               for e in row["temporal"].get("supersession") or ())


def current_order(rows, sources=None, window=None, moved_on=frozenset()):
    """Order by validity and relevance; use claim freshness only on ties, never update time.

    `window` 가 있으면 관련도 순 상위 `window` 행 안에서만 세 묶음으로 세운다(묶음 안은 관련도 순):
    구간이 지금인 행, 나머지, 근거 있게 대체된 행(:func:`superseded_old_end`). `window=0` 은
    융합 전 팔이다: 관련도 순에서 그 세 번째 묶음만 맨 뒤로 보낸다. 어느 쪽도 숨기지 않고 `claim_status`
    도 그대로다. 없으면(청크 풀) 전체를 구간 먼저 세운다. 노드는 종류마다 구간 유무가 달라서
    전체를 구간 먼저 세우면 덜 맞는 기록·결정이 넓은 후보에서 올라와 상위를 채운다.
    근거: [[record:kind-leaks-on-the-81-row-gold-set-current-sort-81-fields-73-graph-arm-61-tie-break-17-id-prefix-0-2026-11-11]]"""
    sources = sources or {}
    def relevance(r):
        if "rrf_score" in r or "score" in r:
            return (float(r.get("rrf_score") or r.get("score") or 0),)
        return (r.get("hit_tokens", 0), r.get("title_tokens", 0), -float(r.get("distance") or 0))
    if window is None:
        rows.sort(key=lambda r: (r["temporal"]["validity"] != "current", tuple(-v for v in relevance(r))))
    else:
        current_ids = frozenset(r.get("id") for r in rows if r["temporal"]["validity"] == "current")
        demote = lambda r: superseded_old_end(r, current_ids, moved_on)  # noqa: E731
        rows.sort(key=lambda r: tuple(-v for v in relevance(r)))
        rows[:window] = sorted(rows[:window], key=lambda r: 2 if demote(r)
                               else 0 if r["temporal"]["validity"] == "current" else 1)
    groups = {}
    for i, row in enumerate(rows):
        key = claim_key(sources.get(row.get("id"), {}))
        if key and row["temporal"]["interval_known"]:
            groups.setdefault((row.get("kind"), key, relevance(row), row["temporal"]["validity"]), []).append(i)
    for positions in groups.values():
        ordered = sorted((rows[i] for i in positions), key=lambda r: instant(r["temporal"]["valid_from"]) or dt.datetime.min.replace(tzinfo=dt.timezone.utc), reverse=True)
        for i, row in zip(positions, ordered):
            rows[i] = row
    if window == 0:
        rows.sort(key=demote)


def finish_nodes(store, hits, *, owner=None, scope=None, current=False, known_at=None, stats=None,
                 window=None):
    sources = node_sources(store, [h.get("id") for h in hits], owner=owner)
    edges, seen_edges = [], set()
    exhausted = True
    # Read only bounded connected lineage; abstain on supersession if the limit is reached.
    for _ in range(8):
        found = supersession_edges(store, sources, owner=owner)
        for edge in found:
            key = (edge["new"], edge["old"])
            if key not in seen_edges:
                seen_edges.add(key)
                edges.append(edge)
        lineage = {e[k] for e in edges for k in ("new", "old")}
        missing = lineage - sources.keys()
        if not missing:
            exhausted = False
            break
        # 상한은 계보의 크기로 잰다. 후보(seed)를 세면 계보가 없어도 판정을 포기한다.
        if len(lineage) > LINEAGE_CAP:
            break
        loaded = node_sources(store, missing, owner=owner)
        if not loaded:
            break
        sources.update(loaded)
    for hit in hits:
        hit.setdefault("kind", sources.get(hit.get("id"), {}).get("kind", ""))
        stamp(hit, sources.get(hit.get("id"), hit), scope=scope)
    if known_at:
        until = instant(time_scope(known_at)[1])
        if stats is not None:
            stats.setdefault("known_hidden", set()).update(h["id"] for h in hits if instant(h["temporal"]["created_at"]) is not None and instant(h["temporal"]["created_at"]) >= until)
            stats.setdefault("known_unknown", set()).update(h["id"] for h in hits if instant(h["temporal"]["created_at"]) is None)
        hits[:] = [h for h in hits if instant(h["temporal"]["created_at"]) is None or instant(h["temporal"]["created_at"]) < until]
    represented = {h["id"] for h in hits if h.get("id")}
    context = list(hits)
    for nid, source in sources.items():
        if nid not in represented:
            extra = stamp({"id": nid, "kind": source["kind"]}, source, scope=scope)
            created = instant(extra["temporal"]["created_at"])
            if not known_at or created is None or created < instant(time_scope(known_at)[1]):
                context.append(extra)
    retired = annotate_claims(context, sources, [] if exhausted else edges,
                              scope=None if current else scope)
    if exhausted:
        for hit in hits:
            hit["temporal"]["supersession_incomplete"] = True
            hit["temporal"]["claim_status"] = "unknown"
    if current:
        if stats is not None:
            stats.setdefault("current_hidden", set()).update(h["id"] for h in hits if h["id"] in retired or h["temporal"]["validity"] in ("historical", "future"))
        hits[:] = [h for h in hits if h["id"] not in retired and h["temporal"]["validity"] not in ("historical", "future")]
        moved_on = chain_moved_on(context)
        current_order(hits, sources, window=window, moved_on=moved_on)
        if stats is not None and window is not None:
            # 재채점이 다시 세운 뒤에도 같은 행을 내리도록 넘긴다(`vinc_rag.retrieval.search`).
            ids = frozenset(h["id"] for h in hits if h["temporal"]["validity"] == "current")
            stats["old_ends"] = {h["id"] for h in hits if superseded_old_end(h, ids, moved_on)}
    return hits


def finish_docs(store, rows, *, owner=None, scope=None, current=False, known_at=None):
    """Carry the document mirror's evidenced lineage to every chunk of that document."""
    docs = {r.get("doc_id"): r for r in rows if r.get("doc_id")}
    hits = [{"id": mirror_id(did), "kind": "concept",
             **{key: row.get("temporal", row).get(key) for key in FIELDS}}
            for did, row in docs.items()]
    finish_nodes(store, hits, owner=owner, scope=scope, current=current, known_at=known_at)
    states = {doc_id_of(h["id"]): h["temporal"] for h in hits}
    if current:
        rows[:] = [r for r in rows if r.get("doc_id") in states]
    for row in rows:
        if row.get("doc_id") in states:
            row["temporal"] = dict(states[row["doc_id"]])
