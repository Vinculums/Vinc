"""평가 카드: 역할에 붙는 결과 Record 하나, 새 카드는 옛 카드를 대체하고 지우지 않는다."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from conftest import RO_KEY, SK_KEY, Recorder, ok
from vinc_client import (AsyncVincClient, VincClient, build_eval_card_fragment, current_cards,
                         record_eval_card)
from vinc_client.eval_card import CARD_PREFIX, card_id

ROLE = "concept:agent-dev-core"
SUITE = {"id": "agent-tasks-test", "hash": "8c" * 32, "tasks": 40}
CELL = {"adapter": "langgraph", "model_family": "claude", "tasks": 40, "runs_per_task": 3,
        "pass_1": {"k": 90, "n": 120}, "pass_k": {"k": 21, "n": 40},
        "violation": {"k": 2, "n": 56}, "fabrication": {"k": 1, "n": 24},
        "cost_per_success_usd": 0.0412}
BASE = {"suite": SUITE, "results": [CELL], "store_fingerprint": "m3-ort130-2026-09-28",
        "harness_version": "agent-tasks-1", "date": "2026-10-26"}


def test_a_card_is_one_result_record_attached_to_its_role():
    fragment = build_eval_card_fragment(ROLE, **BASE)
    card, role = fragment["nodes"][:2]
    assert card["id"].startswith(CARD_PREFIX + "agent-dev-core-2026-10-26-")
    assert card["type"] == "record" and card["tags"] == ["eval-card"]
    assert card["valid_from"] == "2026-10-26" and card["domain"] == "vinc/agent/harness"
    assert role == {"id": ROLE, "type": "concept"}
    props = card["props"]
    assert props["claim_kind"] == "result" and props["role"] == ROLE
    assert props["suite"] == SUITE and props["store_fingerprint"] == "m3-ort130-2026-09-28"
    cell = props["results"][0]
    assert cell["pass_1"] == {"k": 90, "n": 120, "rate": 0.75}
    assert cell["pass_k"]["rate"] == 0.525 and cell["fabrication"]["rate"] == 0.0417
    assert fragment["edges"] == [{"from": card["id"], "to": ROLE, "type": "relates_to",
                                  "evidence": fragment["edges"][0]["evidence"]}]
    assert "agent-tasks-test" in fragment["edges"][0]["evidence"]


def test_the_id_follows_the_measurement_so_a_retry_merges_and_a_new_run_does_not():
    again = build_eval_card_fragment(ROLE, **BASE)["nodes"][0]["id"]
    assert again == build_eval_card_fragment(ROLE, **BASE)["nodes"][0]["id"]
    other_day = build_eval_card_fragment(ROLE, **{**BASE, "date": "2026-10-27"})["nodes"][0]["id"]
    other_model = build_eval_card_fragment(
        ROLE, **{**BASE, "results": [{**CELL, "model_family": "gpt"}]})["nodes"][0]["id"]
    assert len({again, other_day, other_model}) == 3
    assert card_id(ROLE, SUITE, [CELL], store_fingerprint="x", harness_version="y",
                   date="2026-10-26") != again


def test_a_newer_card_supersedes_the_old_one_without_deleting_it():
    old = CARD_PREFIX + "agent-dev-core-2026-10-01-aaaaaaaa"
    fragment = build_eval_card_fragment(ROLE, supersedes=[old, old], **BASE)
    new = fragment["nodes"][0]["id"]
    assert {"id": old, "type": "record", "valid_until": "2026-10-26"} in fragment["nodes"]
    assert [e for e in fragment["edges"] if e["type"] == "supersedes"] == [
        {"from": new, "to": old, "type": "supersedes", "evidence": fragment["edges"][1]["evidence"]}]
    assert not any("props" in n for n in fragment["nodes"][2:])
    # 같은 카드를 다시 쓰는 재시도는 자기 자신을 대체하지 않는다.
    self_only = build_eval_card_fragment(ROLE, supersedes=[new], **BASE)
    assert len(self_only["nodes"]) == 2 and len(self_only["edges"]) == 1


def test_frontier_points_are_kept_and_checked():
    points = [{"label": "full prefetch", "success": 0.75, "tokens_in_mean": 61000},
              {"label": "brief only", "success": 0.7, "tokens_in_mean": 9000}]
    card = build_eval_card_fragment(ROLE, frontier=points, **BASE)["nodes"][0]
    assert card["props"]["frontier"] == points
    for bad in ([{"label": "x", "success": 1.5, "tokens_in_mean": 1}],
                [{"label": "x", "success": 0.5, "tokens_in_mean": -1}],
                [{"success": 0.5, "tokens_in_mean": 1}], ["x"]):
        with pytest.raises(ValueError):
            build_eval_card_fragment(ROLE, frontier=bad, **BASE)


@pytest.mark.parametrize("patch, message", [
    ({"suite": {**SUITE, "hash": "abc"}}, "sha256"),
    ({"suite": {"id": "", "hash": SUITE["hash"], "tasks": 1}}, "suite.id"),
    ({"results": []}, "at least one"),
    ({"results": [CELL, CELL]}, "once"),
    ({"results": [{**CELL, "pass_1": {"k": 5, "n": 4}}]}, "exceed"),
    ({"results": [{**CELL, "pass_k": {"k": 1}}]}, "pass_k.n"),
    ({"results": [{**CELL, "runs_per_task": 0}]}, "at least 1"),
    ({"results": [{**CELL, "cost_per_success_usd": -1}]}, "cost"),
    ({"date": "26 Oct"}, "YYYY-MM-DD"),
    ({"store_fingerprint": " "}, "store_fingerprint"),
])
def test_bad_input_is_refused_before_anything_is_built(patch, message):
    with pytest.raises(ValueError, match=message):
        build_eval_card_fragment(ROLE, **{**BASE, **patch})
    with pytest.raises(ValueError, match="role"):
        build_eval_card_fragment("concept:not-a-role", **BASE)
    with pytest.raises(ValueError, match="eval card ids"):
        build_eval_card_fragment(ROLE, supersedes=["record:other"], **BASE)


def test_unmeasured_numbers_stay_unknown_instead_of_zero():
    cell = {**CELL, "violation": None, "fabrication": {"k": 0, "n": 0}, "cost_per_success_usd": None}
    built = build_eval_card_fragment(ROLE, **{**BASE, "results": [cell]})["nodes"][0]["props"]["results"][0]
    assert built["violation"] is None and built["cost_per_success_usd"] is None
    assert built["fabrication"] == {"k": 0, "n": 0, "rate": None}


def cards_payload(*cards):
    return {"kind": "eval_cards", "executed": True, "role": ROLE, "cards": list(cards)}


OLD = {"id": CARD_PREFIX + "agent-dev-core-2026-10-01-aaaaaaaa", "current": True,
       "suite": {"id": "agent-tasks-test"}}
OTHER_SUITE = {"id": CARD_PREFIX + "agent-dev-core-2026-10-02-bbbbbbbb", "current": True,
               "suite": {"id": "agent-tasks-search"}}
RETIRED = {"id": CARD_PREFIX + "agent-dev-core-2026-09-01-cccccccc", "current": False,
           "suite": {"id": "agent-tasks-test"}}


def test_eval_cards_reads_the_role_route_and_current_cards_keep_the_live_ones():
    rec = Recorder({("GET", f"/v1/roles/{ROLE}/eval-cards"): ok(cards_payload(OLD, OTHER_SUITE, RETIRED))})
    client = VincClient(RO_KEY, transport=httpx.MockTransport(rec))
    assert [c["id"] for c in current_cards(client, ROLE)] == [OLD["id"], OTHER_SUITE["id"]]
    assert [c["id"] for c in current_cards(client, ROLE, suite_id="agent-tasks-test")] == [OLD["id"]]
    assert rec.requests[0].url.path == f"/v1/roles/{ROLE}/eval-cards"

    async def go():
        async with AsyncVincClient(RO_KEY, transport=httpx.MockTransport(rec)) as aclient:
            return await aclient.eval_cards(ROLE, limit=5)

    assert asyncio.run(go())["cards"][0]["id"] == OLD["id"]
    assert rec.requests[-1].url.params["limit"] == "5"


def test_record_eval_card_supersedes_only_the_current_cards_of_the_same_suite():
    written = {"merged": {"nodes": {"created": 1, "matched": 1}, "edges": {"created": 2}},
               "stored": [], "warnings": []}
    rec = Recorder({("GET", f"/v1/roles/{ROLE}/eval-cards"): ok(cards_payload(OLD, OTHER_SUITE, RETIRED)),
                    ("POST", "/v1/fragments"): ok(written)})
    client = VincClient(SK_KEY, transport=httpx.MockTransport(rec))
    assert record_eval_card(client, ROLE, **BASE) == written
    body = rec.body()
    supersedes = [e["to"] for e in body["edges"] if e["type"] == "supersedes"]
    assert supersedes == [OLD["id"]]
    assert {"id": OLD["id"], "type": "record", "valid_until": "2026-10-26"} in body["nodes"]
    with pytest.raises(TypeError, match="supersede"):
        record_eval_card(client, ROLE, supersedes=[OLD["id"]], **BASE)


def test_a_read_only_key_cannot_write_a_card():
    rec = Recorder({("GET", f"/v1/roles/{ROLE}/eval-cards"): ok(cards_payload())})
    client = VincClient(RO_KEY, transport=httpx.MockTransport(rec))
    with pytest.raises(Exception, match="read-only|vinc_sk_|key"):
        record_eval_card(client, ROLE, **BASE)
