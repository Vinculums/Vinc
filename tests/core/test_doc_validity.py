"""문서 유효구간(v22): 3값 쓰기, 메타만 바꾸기, 검색에서 은퇴한 문서 감추기, 동기화.

근거: [[vinc.plan.temporal-edges-point-in-time-document-validity-2026-09-28]] §3
"""
from __future__ import annotations

import os

import pytest

from api import docstore, docsync, query
from store.backend import open_store
from store.config import load_config

OWNER = "user:doc-validity-owner"
TEXT = "# Plan\n\nPoint-in-time reads are not built yet. Zebrafish marker.\n"


@pytest.fixture()
def st(tmp_path):
    keys = ("MEMORY_STORE_BACKEND", "MEMORY_EMBEDDING_MODEL")
    previous = {key: os.environ.get(key) for key in keys}
    os.environ.update(MEMORY_STORE_BACKEND="embedded", MEMORY_EMBEDDING_MODEL="local-en")
    cfg = load_config({"data_dir": str(tmp_path / "db")})
    store = open_store(cfg)
    store.provision(dim=cfg.embedding_dim)
    try:
        yield store, tmp_path
    finally:
        store.close()
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def put(store, **kw):
    return docstore.put_doc(store, owner=OWNER, text=TEXT, doc_id="plan.old", title="Old plan",
                            **kw)


def test_validity_is_three_valued_and_metadata_only(st):
    store, _ = st
    put(store, valid_from="2026-09-28")
    got = docstore.get_doc(store, "plan.old", owner=OWNER)
    assert (got["valid_from"], got["valid_until"], got["validity"]) == ("2026-09-28", "", "current")
    res = put(store, valid_until="2026-10-07", superseded_by="plan.new")
    assert res["skipped"] is False and res["replaced"] == 0, res      # 본문은 다시 안 쓴다
    got = docstore.get_doc(store, "plan.old", owner=OWNER)
    assert got["valid_from"] == "2026-09-28"                           # 키 부재 = 보존
    assert (got["valid_until"], got["superseded_by"], got["validity"]) == (
        "2026-10-07", "plan.new", "historical")
    assert got["verified"] is True and got["text"] == TEXT
    put(store, valid_until="")                                         # 빈 값 = 지움
    assert docstore.get_doc(store, "plan.old", owner=OWNER)["validity"] == "current"


def test_bad_values_are_refused_not_cleared(st):
    store, _ = st
    put(store, valid_until="2026-10-07")
    assert "invalid valid_until" in put(store, valid_until="next week")["error"]
    assert "different document" in put(store, superseded_by="plan.old")["error"]
    assert docstore.get_doc(store, "plan.old", owner=OWNER)["valid_until"] == "2026-10-07"


@pytest.mark.parametrize("detail", ["summary", "full"])
def test_search_hides_chunks_of_retired_documents_by_default(st, detail):
    store, _ = st
    put(store, valid_from="2026-09-28", valid_until="2026-10-07")
    hidden = query.search("Zebrafish marker", owner=OWNER, detail=detail, store=store)
    assert hidden["rows"] == []
    assert hidden["doc_validity_filtered"] >= 1
    shown = query.search("Zebrafish marker", owner=OWNER, historical=True, detail=detail, store=store)
    assert [r for r in shown["rows"] if r.get("doc_id") == "plan.old"]
    then = query.search("Zebrafish marker", owner=OWNER, as_of="2026-10-01", detail=detail, store=store)
    assert [r for r in then["rows"] if r.get("doc_id") == "plan.old"]


def test_docsync_carries_validity_including_a_metadata_only_change(st):
    store, tmp = st
    put(store)
    cfg = load_config({"data_dir": str(tmp / "peer")})
    peer = open_store(cfg)
    peer.provision(dim=cfg.embedding_dim)
    try:
        docsync.import_docs(peer, owner=OWNER, payload=docsync.export_docs(
            store, owner=OWNER, since=0))
        assert docstore.get_doc(peer, "plan.old", owner=OWNER)["validity"] == "current"
        put(store, valid_until="2026-10-07", superseded_by="plan.new")
        delta = docsync.export_docs(store, owner=OWNER, since=0)
        docsync.import_docs(peer, owner=OWNER, payload=delta)
        got = docstore.get_doc(peer, "plan.old", owner=OWNER)
        assert (got["valid_until"], got["superseded_by"]) == ("2026-10-07", "plan.new"), got
    finally:
        peer.close()


@pytest.mark.parametrize("detail", [None, "summary", "full", " FULL ", "unknown"])
def test_search_document_validity_keeps_current_rows_and_detail_contract(st, detail):
    store, _ = st
    put(store, valid_from="2020-09-28", valid_until="2020-10-07")
    current_text = "# Current plan\n\nZebrafish marker. " + "Current guidance. " * 30
    docstore.put_doc(store, owner=OWNER, text=current_text, doc_id="plan.current",
                    title="Current plan", valid_from="2020-10-08")
    options = {"owner": OWNER, "detail": detail, "store": store}
    all_rows = query.search("Zebrafish marker", historical=True, **options)
    current = query.search("Zebrafish marker", **options)
    assert all_rows["executed"] and current["executed"]
    assert "error" not in all_rows and "error" not in current
    assert {row["doc_id"] for row in all_rows["rows"]} == {"plan.old", "plan.current"}
    assert [row["doc_id"] for row in current["rows"]] == ["plan.current"]
    assert current["doc_validity_filtered"] == 1
    assert current["validity_filtered"] >= 1
    row = current["rows"][0]
    assert row == next(r for r in all_rows["rows"] if r["doc_id"] == "plan.current")
    assert row["id"].startswith("plan.current#")
    assert "score" in row and "arms" in row and "cite_id" in row
    if (detail or "").strip().lower() == "full":
        assert row["text"] == current_text
        assert "doc_title" not in row
        assert "snippet" not in row and "truncated" not in row
    else:
        assert "text" not in row
        assert row["doc_title"] == "Current plan"
        assert len(row["snippet"]) <= 240 and row["truncated"] is True
    assert current["total_count"] == all_rows["total_count"] == 2
    assert current["has_more"] is False and current["next_cursor"] is None
    before = query.search("Zebrafish marker", as_of="2020-10-01", **options)
    assert [row["doc_id"] for row in before["rows"]] == ["plan.old"]
    assert before["doc_validity_filtered"] == 1
    outside = query.search("Zebrafish marker", historical=True, as_of="2020-09-01", **options)
    assert outside["rows"] == [] and outside["doc_validity_filtered"] == 2


@pytest.mark.parametrize("detail", ["summary", "full"])
def test_search_document_validity_preserves_cursor_traversal(st, detail):
    store, _ = st
    put(store, valid_until="2020-10-07")
    for doc_id in ("plan.current", "plan.other"):
        docstore.put_doc(store, owner=OWNER, text=TEXT, doc_id=doc_id, title=doc_id)
    all_rows = query.search("Zebrafish marker", owner=OWNER, detail=detail,
                            historical=True, store=store)
    expected = [row["id"] for row in all_rows["rows"] if row["doc_id"] != "plan.old"]
    cursor, seen, filtered = None, [], 0
    for _ in range(3):
        page = query.search("Zebrafish marker", owner=OWNER, detail=detail,
                            top_k=1, cursor=cursor, store=store)
        assert page["executed"] and "error" not in page
        assert page["total_count"] == 3
        seen.extend(row["id"] for row in page["rows"])
        filtered += page.get("doc_validity_filtered", 0)
        if not page["has_more"]:
            assert page["next_cursor"] is None
            break
        assert page["next_cursor"]
        cursor = page["next_cursor"]
    else:
        pytest.fail("cursor traversal did not finish")
    assert seen == expected and len(set(seen)) == len(seen)
    assert filtered == 1
