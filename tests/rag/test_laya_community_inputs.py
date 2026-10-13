import pytest
from rag_eval.laya.community_inputs import assert_disjoint


def pair(nid, text, origin=None, context=None):
    return {"a": {"id": nid, "text": text}, "b": {"id": nid + ":b", "text": text + " b"}, "provenance": {"a": {"node_id": origin}}, "context": context}


@pytest.mark.parametrize("eval_pair", [pair("train", "new"), pair("eval", "train text"), pair("eval", "new", "shared-origin"), pair("eval", "new", context="train text")])
def test_blocks_leakage_by_fixture_origin_text_and_context(eval_pair):
    with pytest.raises(ValueError, match="overlap"):
        assert_disjoint({"train": [pair("train", "train text", "shared-origin")], "eval": [eval_pair]})


def test_disjoint_public_inputs_are_allowed():
    assert_disjoint({"train": [pair("train", "train text")], "calib": [pair("calib", "calib text")], "eval": [pair("eval", "eval text")]})
