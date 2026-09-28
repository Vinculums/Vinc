from __future__ import annotations

import re

import pytest

from conftest import RESOLVED
from vinc_client import BOUNDARY, build_episode_fragment, fence, format_brief, format_search


def test_fence_is_longer_than_any_backtick_run_inside():
    body = "before\n````\ncode\n````\nafter"
    fenced = fence(body)
    assert fenced.startswith("`````\n") and fenced.endswith("\n`````")


def test_resolved_brief_carries_node_grounds_excerpts_and_evidence():
    block = format_brief(RESOLVED)
    assert block.startswith(BOUNDARY)
    for needle in ("Node: Colour tokens are stored as OKLCH (decision:tokens-are-oklch)",
                   "Grounds: decision:tokens-are-oklch, record:contrast-audit",
                   "Design tokens § Colour (vinc.design.tokens)",
                   "The audit measured every pair."):
        assert needle in block


def test_none_renders_nothing():
    assert format_brief({"status": "none"}) is None


def test_choose_lists_candidates_without_picking_one():
    block = format_brief({"status": "choose", "candidates": [
        {"id": "concept:a", "title": "Spacing"}, {"id": "concept:b", "title": "Spacing scale"}]})
    assert "none was chosen" in block and "concept:a" in block and "concept:b" in block


def test_budget_is_respected_and_says_it_cut():
    long = dict(RESOLVED, excerpts=[{"doc_id": f"d{i}", "doc_title": "T", "excerpt": "x" * 500}
                                    for i in range(40)])
    block = format_brief(long, max_chars=1500)
    assert len(block) <= 1500
    assert "truncated" in block


def test_a_cut_right_after_a_section_head_drops_the_head():
    long = dict(RESOLVED, excerpts=[{"doc_id": "d", "doc_title": "T", "excerpt": "x" * 590}])
    block = format_brief(long, max_chars=len(BOUNDARY) + 12 + 300)
    lines = block.splitlines()
    assert "Excerpts:" not in lines
    assert lines[-2] == "(truncated to fit the context budget)"


def test_search_fallback_renders_hits_and_rows():
    block = format_search({"node_hits": [{"id": "concept:s", "title": "Spacing", "kind": "concept"}],
                           "rows": [{"doc_id": "vinc.design.tokens", "doc_title": "Tokens",
                                     "snippet": "spacing uses a 4px base"}]})
    assert "concept:s" in block and "4px base" in block
    assert format_search({"rows": [], "node_hits": []}) is None


def test_episode_fragment_shape():
    frag = build_episode_fragment("Merged the contrast fix", about=["decision:d", "concept:c"],
                                  date="2026-09-28")
    episode = frag["nodes"][0]
    assert re.fullmatch(r"episode:2026-09-28-merged-the-contrast-fix-[0-9a-f]{6}", episode["id"])
    assert frag["nodes"][1:] == [{"id": "decision:d", "type": "decision"},
                                 {"id": "concept:c", "type": "concept"}]
    assert all("props" not in node for node in frag["nodes"])


def test_two_same_day_episodes_with_one_title_get_different_ids():
    a = build_episode_fragment("Deployed", date="2026-09-28")["nodes"][0]["id"]
    b = build_episode_fragment("Deployed", date="2026-09-28")["nodes"][0]["id"]
    assert a != b


@pytest.mark.parametrize("about", [["record:r"], ["episode:e"], ["plain-text"]])
def test_about_targets_must_be_concepts_or_decisions(about):
    with pytest.raises(ValueError):
        build_episode_fragment("x", about=about, date="2026-09-28")


def test_date_and_title_are_checked():
    with pytest.raises(ValueError):
        build_episode_fragment("x", date="28/09/2026")
    with pytest.raises(ValueError):
        build_episode_fragment("  ", date="2026-09-28")


def test_a_korean_title_still_gets_a_readable_id():
    frag = build_episode_fragment("대비 수정 머지", date="2026-09-28")
    assert frag["nodes"][0]["id"].startswith("episode:2026-09-28-episode-")
    assert frag["nodes"][0]["title"] == "대비 수정 머지"


def test_combined_answer_does_not_repeat_tied_candidates():
    from vinc_client import format_answer
    tie = {"status": "choose", "candidates": [{"id": "concept:a", "title": "A"}]}
    search = {"node_hits": [{"id": "concept:a", "title": "A", "kind": "concept"},
                            {"id": "concept:b", "title": "B", "kind": "concept"}], "rows": []}
    block = format_answer(tie, search)
    assert block.count("(concept:a)") == 1 and "(concept:b)" in block
