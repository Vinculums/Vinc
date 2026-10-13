"""Search time metadata and explicit modes, without changing bodies or stored intervals."""
from __future__ import annotations

import datetime as dt
import json
import re

from store import pkns
from store.querybase import _props_to_obj, holds_during, resolve_as_of, time_scope

FIELDS = ("created_at", "updated_at", "content_time", "valid_from", "valid_until",
          "superseded_by")
KINDS = {"concept": ("Concept", "concept_id"), "record": ("Record", "record_id"),
         "decision": ("Decision", "decision_id"), "episode": ("Episode", "episode_id")}


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
    elif vf and now < vf:
        state = "future"
    elif not vf and not vu:
        state = "unknown"
    else:
        state = "current"
    out.update(validity=state, interval_known=bool(vf or vu) and not invalid,
               interval_invalid=bool(invalid),
               holds_in_scope=(holds_during(out["valid_from"], out["valid_until"], scope)
                               if scope and not invalid else None),
               supersession=[], supersedes=[], claim_status="unscoped", conflicts=[])
    return out


def stamp(row, source=None, **kw):
    row["temporal"] = metadata(source if source is not None else row, **kw)
    # Keep legacy detail strings and fill only absent search fields.
    for key in FIELDS:
        row.setdefault(key, row["temporal"][key])
    row.setdefault("validity", row["temporal"]["validity"])
    return row


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
    mirrors = {nid: nid[len("concept:doc."):] for nid in out if nid.startswith("concept:doc.")}
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
            if kind == "concept" and not all(i.startswith("concept:doc.") for i in (new, old)):
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


def current_order(rows, sources=None):
    """Order by validity and relevance; use claim freshness only on ties, never update time."""
    sources = sources or {}
    def relevance(r):
        if "rrf_score" in r or "score" in r:
            return (float(r.get("rrf_score") or r.get("score") or 0),)
        return (r.get("hit_tokens", 0), r.get("title_tokens", 0), -float(r.get("distance") or 0))
    rows.sort(key=lambda r: (r["temporal"]["validity"] != "current", tuple(-v for v in relevance(r))))
    groups = {}
    for i, row in enumerate(rows):
        key = claim_key(sources.get(row.get("id"), {}))
        if key and row["temporal"]["interval_known"]:
            groups.setdefault((row.get("kind"), key, relevance(row), row["temporal"]["validity"]), []).append(i)
    for positions in groups.values():
        ordered = sorted((rows[i] for i in positions), key=lambda r: instant(r["temporal"]["valid_from"]) or dt.datetime.min.replace(tzinfo=dt.timezone.utc), reverse=True)
        for i, row in zip(positions, ordered):
            rows[i] = row


def finish_nodes(store, hits, *, owner=None, scope=None, current=False, known_at=None, stats=None):
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
        missing = {e[k] for e in edges for k in ("new", "old")} - sources.keys()
        if not missing:
            exhausted = False
            break
        if len(sources) + len(missing) > 256:
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
        current_order(hits, sources)
    return hits


def finish_docs(store, rows, *, owner=None, scope=None, current=False, known_at=None):
    """Carry the document mirror's evidenced lineage to every chunk of that document."""
    docs = {r.get("doc_id"): r for r in rows if r.get("doc_id")}
    hits = [{"id": "concept:doc." + did, "kind": "concept",
             **{key: row.get("temporal", row).get(key) for key in FIELDS}}
            for did, row in docs.items()]
    finish_nodes(store, hits, owner=owner, scope=scope, current=current, known_at=known_at)
    states = {h["id"][len("concept:doc."):]: h["temporal"] for h in hits}
    if current:
        rows[:] = [r for r in rows if r.get("doc_id") in states]
    for row in rows:
        if row.get("doc_id") in states:
            row["temporal"] = dict(states[row["doc_id"]])
