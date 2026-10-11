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


def test_search_hides_chunks_of_retired_documents_by_default(st):
    store, _ = st
    put(store, valid_from="2026-09-28", valid_until="2026-10-07")
    hidden = query.search("Zebrafish marker", owner=OWNER, store=store)
    assert not [r for r in hidden["rows"] if r.get("doc_id") == "plan.old"], hidden["rows"]
    assert hidden["doc_validity_filtered"] >= 1
    shown = query.search("Zebrafish marker", owner=OWNER, historical=True, store=store)
    assert [r for r in shown["rows"] if r.get("doc_id") == "plan.old"]
    then = query.search("Zebrafish marker", owner=OWNER, as_of="2026-10-01", store=store)
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
