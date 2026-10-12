"""스펙 주장 계약과 낡은 주장 보고.

근거: [[vinc.plan.temporal-edges-point-in-time-document-validity-2026-09-28]]
"""
from __future__ import annotations

import os

import pytest

from api import fragment, query, spec_contract
from store.backend import open_store
from store.config import load_config

OWNER = "user:spec-owner"


def claim(**over):
    props = {"spec_schema": "vinc.specclaim.v1", "feature": "concept:as-of",
             "doc_id": "vinc.plan.temporal", "text": "as_of is on search and brief",
             "subject_path": "vinc:store/querybase.py", "measured_at": "2026-10-07",
             "measured_ref": "dce4889", "review": "verified"}
    props.update(over)
    return {"id": "record:specclaim-as-of-search-v2", "type": "record",
            "title": "as_of is on search and brief", "props": props}


@pytest.mark.parametrize("over,reason", [
    ({"review": "done"}, "review must be"),
    ({"review": "approved"}, "approved_by"),
    ({"feature": "record:x"}, "feature must be"),
    ({"measured_at": "2026-10"}, "measured_at must be"),
    ({"subject_path": "store/querybase.py"}, "repo:path"),
    ({"text": " "}, "requires text"),
])
def test_claim_contract_refuses_with_a_reason(over, reason):
    with pytest.raises(spec_contract.InvalidSpecRecord, match=reason):
        spec_contract.validate(claim(**over))


def test_anyone_may_approve_when_the_approver_is_recorded():
    ok = claim(review="approved", approved_by="concept:agent-steward", approved_at="2026-10-11")
    assert spec_contract.validate(ok) == spec_contract.CLAIM


def test_reserved_prefix_without_schema_and_ordinary_records():
    with pytest.raises(spec_contract.InvalidSpecRecord, match="reserved"):
        spec_contract.validate({"id": "record:specclaim-x", "type": "record", "props": {}})
    assert spec_contract.validate({"id": "record:note", "type": "record", "props": {}}) is None


def test_model_drift_candidates_cannot_claim_verification():
    drift = {"id": "record:specdrift-abc", "type": "record", "props": {
        "spec_schema": "vinc.specdrift.v1", "claim": "record:specclaim-x",
        "evidence": "episode:change-vinc-pr-673", "observed": "as_of exists",
        "generated_by": "model", "review": "verified"}}
    with pytest.raises(spec_contract.InvalidSpecRecord, match="review must be"):
        spec_contract.validate(drift)
    drift["props"]["review"] = "candidate"
    assert spec_contract.validate(drift) == spec_contract.DRIFT


def test_verification_needs_a_named_check_and_a_result():
    ver = {"id": "record:specverify-abc", "type": "record", "props": {
        "spec_schema": "vinc.specverify.v1", "claim": "record:specclaim-x",
        "check": "tests/core/test_as_of.py", "run_ref": "ci-1", "measured_ref": "46c1546",
        "measured_at": "2026-10-11", "subject_path": "vinc:store/querybase.py", "result": "ok"}}
    with pytest.raises(spec_contract.InvalidSpecRecord, match="pass or fail"):
        spec_contract.validate(ver)


@pytest.fixture(scope="module")
def st(tmp_path_factory):
    keys = ("MEMORY_STORE_BACKEND", "MEMORY_EMBEDDING_MODEL")
    previous = {key: os.environ.get(key) for key in keys}
    os.environ.update(MEMORY_STORE_BACKEND="embedded", MEMORY_EMBEDDING_MODEL="local-en")
    cfg = load_config({"data_dir": str(tmp_path_factory.mktemp("spec") / "db")})
    store = open_store(cfg)
    store.provision(dim=cfg.embedding_dim)
    try:
        yield store
    finally:
        store.close()
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_authored_ingest_refuses_and_sync_path_does_not(st):
    bad = {"owner": OWNER, "nodes": [claim(review="approved")]}
    out = fragment.merge_fragment(bad, store=st, authored=True)
    assert out["executed"] is False and out["error"] == "invalid_spec_record", out
    out = fragment.merge_fragment(bad, store=st)
    assert out["executed"] is not False, out


def change(eid, date, files, sha):
    return {"id": eid, "type": "episode", "title": eid, "date": date,
            "props": {"source_kind": "pr", "files": files, "head_sha": sha}}


def test_stale_reports_claims_whose_path_changed_after_measurement(st):
    nodes = [
        claim(),
        {**claim(subject_path="vinc:api/sync.py", measured_at="2026-10-01", measured_ref="aaa"),
         "id": "record:specclaim-sync-v1"},
        {**claim(subject_path="vinc:api", measured_at="2026-10-01", measured_ref="bbb"),
         "id": "record:specclaim-api-dir-v1", "valid_until": "2026-10-02"},
        change("episode:change-vinc-pr-1", "2026-10-07", ["vinc:store/querybase.py"], "dce4889"),
        change("episode:change-vinc-pr-2", "2026-10-09", ["vinc:api/sync.py"], "ccc"),
        change("episode:change-vinc-pr-3", "2026-09-30", ["vinc:api/sync.py"], "ddd"),
    ]
    out = fragment.merge_fragment({"owner": OWNER, "nodes": nodes}, store=st, authored=True)
    assert out["executed"] is not False, out
    got = query.claim_diagnosis(owner=OWNER, store=st)
    stale = {s["claim"]["id"]: s for s in got["stale"]}
    # 같은 커밋에서 잰 주장, 은퇴한 주장, 측정 전 변경은 낡은 것이 아니다.
    assert set(stale) == {"record:specclaim-sync-v1"}, got["stale"]
    hit = stale["record:specclaim-sync-v1"]
    assert hit["change"]["id"] == "episode:change-vinc-pr-2" and hit["changes_since"] == 1
    assert got["stale_count"] == 1


def test_a_resend_without_props_keeps_them_and_is_not_refused(st):
    out = fragment.merge_fragment({"owner": OWNER, "nodes": [claim()]}, store=st, authored=True)
    assert out["executed"] is not False, out
    bare = {"id": "record:specclaim-as-of-search-v2", "type": "record",
            "domain": "vinc/governance/claim-decay"}
    out = fragment.merge_fragment({"owner": OWNER, "nodes": [bare]}, store=st, authored=True)
    assert out["executed"] is not False, out
    assert spec_contract.validate(bare, existing=True) is None
    with pytest.raises(spec_contract.InvalidSpecRecord):
        spec_contract.validate(bare)                        # 저장된 기록이 아니면 면제 없음


def test_same_day_changes_are_flagged_and_short_refs_match_long_shas(st):
    nodes = [
        {**claim(subject_path="vinc:api/server.py", measured_at="2026-10-11",
                 measured_ref="46c1546"), "id": "record:specclaim-server-v1"},
        {**claim(subject_path="vinc:api/docsync.py", measured_at="2026-10-11",
                 measured_ref="1e2d838"), "id": "record:specclaim-docsync-v1"},
        change("episode:change-vinc-pr-749", "2026-10-11",
               ["vinc:api/server.py", "vinc:api/docsync.py"],
               "1e2d838801703bfb4e387948a2aef688876ac558"),
    ]
    out = fragment.merge_fragment({"owner": OWNER, "nodes": nodes}, store=st, authored=True)
    assert out["executed"] is not False, out
    stale = {s["claim"]["id"]: s for s in query.claim_diagnosis(owner=OWNER, store=st)["stale"]}
    assert stale["record:specclaim-server-v1"]["same_day"] is True
    assert "record:specclaim-docsync-v1" not in stale     # 측정한 바로 그 커밋이다


@pytest.mark.parametrize("node", [
    {"id": "record:specclaim-brand-new", "type": "record"},                  # 새 예약 id
    {"id": "record:specclaim-as-of-search-v2", "type": "record", "props": None},
    {"id": "record:specclaim-as-of-search-v2", "type": "record", "props": {}},
])
def test_resend_exemption_is_only_for_an_existing_record_without_a_props_key(st, node):
    fragment.merge_fragment({"owner": OWNER, "nodes": [claim()]}, store=st, authored=True)
    out = fragment.merge_fragment({"owner": OWNER, "nodes": [node]}, store=st, authored=True)
    assert out["executed"] is False and out["error"] == "invalid_spec_record", out


def test_resend_exemption_does_not_cross_owners(st):
    fragment.merge_fragment({"owner": OWNER, "nodes": [claim()]}, store=st, authored=True)
    bare = {"id": "record:specclaim-as-of-search-v2", "type": "record"}
    out = fragment.merge_fragment({"owner": "user:someone-else", "nodes": [bare]},
                                  store=st, authored=True)
    assert out["executed"] is False and out["error"] == "invalid_spec_record", out


def test_resend_keeps_the_saved_props(st):
    fragment.merge_fragment({"owner": OWNER, "nodes": [claim()]}, store=st, authored=True)
    bare = {"id": "record:specclaim-as-of-search-v2", "type": "record",
            "domain": "vinc/governance/claim-decay"}
    out = fragment.merge_fragment({"owner": OWNER, "nodes": [bare]}, store=st, authored=True,
                                  read_back=True)
    assert out["executed"] is not False, out
    got = query._probe_node("record:specclaim-as-of-search-v2", owner=OWNER, cfg=None, store=st)
    assert "vinc.specclaim.v1" in str(got.get("props")), got


def test_branch_refs_compare_exactly_and_only_hashes_by_prefix(st):
    nodes = [
        {**claim(subject_path="vinc:api/sync.py", measured_at="2026-10-11",
                 measured_ref="main"), "id": "record:specclaim-branch-ref-v1"},
        change("episode:change-vinc-main-fix", "2026-10-11", ["vinc:api/sync.py"], "main-fix"),
    ]
    out = fragment.merge_fragment({"owner": OWNER, "nodes": nodes}, store=st, authored=True)
    assert out["executed"] is not False, out
    stale = {s["claim"]["id"] for s in query.claim_diagnosis(owner=OWNER, store=st)["stale"]}
    assert "record:specclaim-branch-ref-v1" in stale     # main 은 main-fix 의 접두가 아니다
