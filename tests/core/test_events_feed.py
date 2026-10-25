"""변경 기록에서 계산하는 이벤트 피드(api/events.py): 세 종류, 커서, 페이지, 소유자 격리.

근거: [[vinc.plan.mcp-events-and-webhooks]]
"""
from __future__ import annotations

import os

import pytest

from api import docstore, events, fragment as frag
from store.backend import open_store
from store.config import load_config

OWNER = "user:events-owner"
OTHER = "user:events-other"
ROLE = "concept:agent-events-watcher"
ROLE_PROPS = {"mission": "Watch events.", "tier": "verify", "rung": "fast",
              "never": ["guess"], "person_only": [], "reads": ["plan.a", "plan.b"]}


@pytest.fixture()
def store(tmp_path):
    keys = ("MEMORY_STORE_BACKEND", "MEMORY_EMBEDDING_MODEL")
    previous = {key: os.environ.get(key) for key in keys}
    os.environ.update(MEMORY_STORE_BACKEND="embedded", MEMORY_EMBEDDING_MODEL="local-en")
    cfg = load_config({"data_dir": str(tmp_path / "db")})
    st = open_store(cfg)
    st.provision(dim=cfg.embedding_dim)
    try:
        yield st
    finally:
        st.close()
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def ingest(store, nodes, edges=(), owner=OWNER):
    res = frag.merge_fragment({"owner": owner, "nodes": list(nodes), "edges": list(edges)},
                              store=store)
    assert res.get("executed") is not False, res
    return res


def with_role(store, owner=OWNER, role=ROLE):
    ingest(store, [{"id": role, "type": "concept", "title": "Events watcher", "domain": "agent",
                    "props": ROLE_PROPS}], owner=owner)


def scan(store, since, **kw):
    out = events.scan(store, owner=kw.pop("owner", OWNER), since=since, **kw)
    assert out["executed"] is True, out
    return out


def names(out):
    return [(e["name"], e["data"].get("change") or e["data"].get("lesson") or e["data"].get("claim"))
            for e in out["events"]]


def test_cursor_round_trip_and_from_now(store):
    assert events.parse_cursor(None) is None and events.parse_cursor("") is None
    assert events.parse_cursor("0") == (0, 0)
    assert events.parse_cursor("12.7") == (12, 7)
    for bad in ("1", "a.b", "-1.0", "1.2.3"):
        with pytest.raises(ValueError):
            events.parse_cursor(bad)
    with_role(store)
    docstore.put_doc(store, owner=OWNER, text="# A\n\nfirst\n", doc_id="plan.a", title="Plan A")
    now = scan(store, None)
    assert now["events"] == [] and now["cursor"] == now["head"]
    assert scan(store, events.parse_cursor(now["cursor"]))["events"] == []


def test_required_reading_changed_updated_retired_deleted(store):
    with_role(store)
    start = events.parse_cursor(scan(store, None)["cursor"])
    docstore.put_doc(store, owner=OWNER, text="# A\n\nfirst\n", doc_id="plan.a", title="Plan A")
    docstore.put_doc(store, owner=OWNER, text="# C\n\nnot read\n", doc_id="plan.c", title="C")
    first = scan(store, start)
    assert names(first) == [("role.reading_changed", "updated")]
    ev = first["events"][0]
    assert ev["data"]["role"] == ROLE and ev["data"]["doc_id"] == "plan.a"
    assert ev["eventId"].startswith("evt_") and ev["timestamp"].endswith("Z")
    # 같은 위치에서 다시 읽으면 같은 id 다(피드와 웹훅이 같은 id 를 낸다).
    assert scan(store, start)["events"][0]["eventId"] == ev["eventId"]
    after = events.parse_cursor(first["cursor"])
    assert scan(store, after)["events"] == []
    docstore.put_doc(store, owner=OWNER, text="# A\n\nsecond\n", doc_id="plan.a")
    second = scan(store, after)
    assert names(second) == [("role.reading_changed", "updated")]
    assert second["events"][0]["eventId"] != ev["eventId"]
    after = events.parse_cursor(second["cursor"])
    docstore.put_doc(store, owner=OWNER, text="# A\n\nsecond\n", doc_id="plan.a",
                     valid_until="2026-10-25", superseded_by="plan.b")
    retired = scan(store, after)
    assert names(retired) == [("role.reading_changed", "retired")]
    assert retired["events"][0]["data"]["superseded_by"] == "plan.b"
    after = events.parse_cursor(retired["cursor"])
    docstore.delete_doc(store, "plan.a", owner=OWNER)
    assert names(scan(store, after)) == [("role.reading_changed", "deleted")]


def test_lesson_added_to_a_role_and_subscription_records_are_not_lessons(store):
    with_role(store)
    start = events.parse_cursor(scan(store, None)["cursor"])
    ingest(store, [
        {"id": "record:lesson-one", "type": "record", "title": "Read the diff first",
         "domain": "agent"},
        {"id": "decision:rule-two", "type": "decision", "title": "Never merge red",
         "domain": "agent"},
        {"id": "record:event-subscription-0123456789abcdef", "type": "record",
         "title": "Event subscription", "domain": "agent"},
        {"id": "record:unattached", "type": "record", "title": "Nothing", "domain": "agent"},
    ], [
        {"from": "record:lesson-one", "to": ROLE, "type": "relates_to", "evidence": "a lesson"},
        {"from": "decision:rule-two", "to": ROLE, "type": "about", "evidence": "a rule"},
        {"from": "record:event-subscription-0123456789abcdef", "to": ROLE, "type": "relates_to",
         "evidence": "a subscription"},
    ])
    out = scan(store, start, kinds=("role.lesson_added",))
    got = sorted((e["data"]["lesson"], e["data"]["lesson_kind"]) for e in out["events"])
    assert got == [("decision:rule-two", "decision"), ("record:lesson-one", "record")]
    first_ids = {e["eventId"] for e in out["events"]}
    # 교훈을 고치면 같은 id 가 다시 나온다(받는 쪽이 webhook-id 로 거른다).
    after = events.parse_cursor(out["cursor"])
    ingest(store, [{"id": "record:lesson-one", "type": "record", "title": "Read the diff twice"}])
    again = scan(store, after, kinds=("role.lesson_added",))
    assert {e["eventId"] for e in again["events"]} <= first_ids and again["events"]


def test_cited_claim_superseded(store):
    with_role(store)
    docstore.put_doc(store, owner=OWNER, text="# B\n\nplan b\n", doc_id="plan.b", title="Plan B")
    ingest(store, [
        {"id": "record:claim-x", "type": "record", "title": "X holds", "domain": "agent",
         "valid_from": "2026-10-01"},
        {"id": "decision:choice-y", "type": "decision", "title": "Choose Y", "domain": "agent"},
        {"id": "episode:run-demo-12345678", "type": "episode", "title": "Run demo",
         "domain": "agent", "date": "2026-10-25",
         "props": {"run_id": "demo", "role": ROLE, "outcome": "success",
                   "cited_ids": ["record:claim-x", "decision:choice-y", "concept:doc.plan.b"]}},
    ])
    start = events.parse_cursor(scan(store, None)["cursor"])
    ingest(store, [{"id": "record:claim-x", "type": "record", "valid_until": "2026-10-25"}])
    ingest(store, [{"id": "decision:choice-z", "type": "decision", "title": "Choose Z",
                    "domain": "agent"}],
           [{"from": "decision:choice-z", "to": "decision:choice-y", "type": "supersedes",
             "evidence": "Z replaces Y"}])
    docstore.put_doc(store, owner=OWNER, text="# B\n\nplan b\n", doc_id="plan.b",
                     valid_until="2026-10-25")
    out = scan(store, start, kinds=("run.citation_superseded",))
    got = {e["data"]["claim"]: e["data"] for e in out["events"]}
    assert set(got) == {"record:claim-x", "decision:choice-y", "concept:doc.plan.b"}
    assert got["record:claim-x"]["valid_until"] == "2026-10-25"
    assert got["decision:choice-y"]["superseded_by"] == "decision:choice-z"
    assert all(d["run"] == "episode:run-demo-12345678" and d["role"] == ROLE
               for d in got.values())


def test_role_filter_kinds_and_paging(store):
    with_role(store)
    ingest(store, [{"id": "concept:agent-other-role", "type": "concept", "title": "Other",
                    "domain": "agent", "props": {**ROLE_PROPS, "reads": ["plan.b"]}}])
    start = events.parse_cursor(scan(store, None)["cursor"])
    for i in range(3):
        docstore.put_doc(store, owner=OWNER, text=f"# A\n\nv{i}\n", doc_id="plan.a")
        docstore.put_doc(store, owner=OWNER, text=f"# B\n\nv{i}\n", doc_id="plan.b")
    everything = scan(store, start)
    assert {e["data"]["role"] for e in everything["events"]} == {ROLE, "concept:agent-other-role"}
    only = scan(store, start, role="agent-other-role")
    assert {e["data"]["doc_id"] for e in only["events"]} == {"plan.b"}
    assert scan(store, start, kinds=("role.lesson_added",))["events"] == []
    # 한 행씩 넘겨도 같은 것을 모두 모은다.
    seen, cur, pages = set(), start, 0
    while True:
        page = scan(store, cur, limit=1)
        seen |= {e["eventId"] for e in page["events"]}
        cur, pages = events.parse_cursor(page["cursor"]), pages + 1
        if not page["has_more"]:
            break
        assert pages < 20
    assert seen == {e["eventId"] for e in everything["events"]} and pages > 1


def test_owner_isolation_and_cursor_ahead_of_head(store):
    with_role(store)
    with_role(store, owner=OTHER, role="concept:agent-other-watcher")
    start = events.parse_cursor(scan(store, None)["cursor"])
    other_start = events.parse_cursor(scan(store, None, owner=OTHER)["cursor"])
    docstore.put_doc(store, owner=OTHER, text="# A\n\nother\n", doc_id="plan.b", title="B")
    assert scan(store, start)["events"] == []
    assert len(scan(store, other_start, owner=OTHER)["events"]) == 1
    ahead = scan(store, (start[0] + 1000, start[1]))
    assert ahead["truncated"] is True and ahead["cursor"] == ahead["head"]


def test_public_shape_drops_positions_and_bad_input_is_refused(store):
    with_role(store)
    start = events.parse_cursor(scan(store, None)["cursor"])
    docstore.put_doc(store, owner=OWNER, text="# A\n\nx\n", doc_id="plan.a", title="A")
    out = scan(store, start)
    assert out["events"][0]["_pos"][0] == "d"
    assert "_pos" not in events.public(out)["events"][0]
    with pytest.raises(ValueError):
        events.parse_kinds("run.answered")
    with pytest.raises(ValueError):
        events.clamp_limit(501)
    assert events.scan(store, owner="", since=None)["executed"] is False
