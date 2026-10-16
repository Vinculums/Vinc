# -*- coding: utf-8 -*-
"""store/canon: 정규 JSON·지문·파일 해시의 한 벌."""
from __future__ import annotations

import hashlib
import json
import math

import pytest

from store import canon


def test_non_ascii_round_trips_unescaped():
    value = {"title": "지식 그래프", "emoji": "\U0001F600", "n": [1, 2.5, None, True]}
    text = canon.canonical_json(value)
    assert "지식 그래프" in text and "\\u" not in text
    assert json.loads(text) == value
    assert canon.digest(value) == hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_nan_and_infinity_are_refused(bad):
    with pytest.raises(ValueError):
        canon.canonical_json({"x": bad})
    with pytest.raises(ValueError):
        canon.digest([bad])


def test_key_order_does_not_change_the_digest():
    a = {"b": 1, "a": {"y": "가", "x": [3, 2]}}
    b = {"a": {"x": [3, 2], "y": "가"}, "b": 1}
    assert canon.canonical_json(a) == canon.canonical_json(b) == '{"a":{"x":[3,2],"y":"가"},"b":1}'
    assert canon.digest(a) == canon.digest(b)


def test_ascii_v1_form_is_pinned_byte_for_byte():
    # 저장된 토픽 이전 지문과 같아야 하므로 옛 형식을 바이트 단위로 고정한다.
    value = {"b": "가", "a": float("nan")}
    assert canon.canonical_json_ascii_v1(value) == '{"a":NaN,"b":"\\uac00"}'
    assert canon.digest_ascii_v1({"t": "가"}) == hashlib.sha256(b'{"t":"\\uac00"}').hexdigest()
    assert canon.digest_ascii_v1({"t": "가"}) != canon.digest({"t": "가"})


def test_transfer_modules_keep_the_v1_form():
    from store import topic_batch_manifest, topic_transfer
    assert topic_transfer._canonical({"t": "가"}) == '{"t":"\\uac00"}'
    assert topic_batch_manifest.digest({"t": "가"}) == canon.digest_ascii_v1({"t": "가"})


def test_shared_form_matches_every_former_copy():
    from api import community_agents, mutation_preview, operation_receipts
    value = {"z": "한글", "a": [1, {"k": None}]}
    expected = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    assert operation_receipts.canonical(value) == expected
    assert mutation_preview._canonical(value) == expected
    assert community_agents.canonical(value) == expected
    assert community_agents.digest(value) == canon.digest(value)


def test_community_wrapper_still_validates():
    from api import community_agents
    with pytest.raises(ValueError, match="snapshot_value_invalid"):
        community_agents.canonical({"x": 1.5})


@pytest.mark.parametrize("size", [0, 1, 1024 * 1024, 3 * 1024 * 1024 + 7])
def test_streamed_file_hash_equals_whole_file_hash(tmp_path, size):
    path = tmp_path / "blob.bin"
    data = bytes(i % 251 for i in range(size))
    path.write_bytes(data)
    whole = hashlib.sha256(data).hexdigest()
    assert canon.file_sha256(path) == whole
    assert canon.file_sha256(path, chunk=4096) == whole
    assert canon.file_sha256_size(str(path)) == (whole, size)
