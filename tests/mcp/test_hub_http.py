"""허브 HTTP 의 공용 본문 읽기(`vinc_mcp.hub_http`)와 두 문 사이에 순환 import 가 없다는 것."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from vinc_mcp import hub_http


class _Never(io.BytesIO):
    def read(self, *_args):
        raise AssertionError("이 본문은 읽으면 안 된다")


def _refused(headers, rfile, limit=hub_http.MAX_BODY):
    with pytest.raises(hub_http.BodyRefused) as err:
        hub_http.read_json_body(headers, rfile, limit)
    return err.value.reason


def test_an_empty_or_missing_length_is_an_empty_object_and_reads_nothing():
    assert hub_http.read_json_body({}, _Never()) == {}
    assert hub_http.read_json_body({"Content-Length": "0"}, _Never()) == {}


def test_a_length_over_the_limit_is_refused_without_reading():
    assert _refused({"Content-Length": str(hub_http.MAX_BODY + 1)}, _Never()) == "too_large"
    assert _refused({"Content-Length": "11"}, _Never(), limit=10) == "too_large"


@pytest.mark.parametrize("length", ["abc", "-5", "1.5"])
def test_a_length_that_is_not_a_count_is_refused(length):
    assert _refused({"Content-Length": length}, _Never()) == "bad_length"


@pytest.mark.parametrize("raw", [b"{nope", b"[]", b'"x"', b"null", b'{"\xff'])
def test_a_body_that_is_not_a_json_object_is_refused(raw):
    assert _refused({"Content-Length": str(len(raw))}, io.BytesIO(raw)) == "not_object"


def test_a_body_that_ends_before_its_length_is_refused():
    assert _refused({"Content-Length": "5"}, io.BytesIO(b"")) == "not_object"


def test_the_json_encodings_the_v1_door_accepts_stay_accepted():
    value = {"query": "한글"}
    for raw in (json.dumps(value).encode(), b"\xef\xbb\xbf" + json.dumps(value).encode(),
                json.dumps(value).encode("utf-16"), json.dumps(value).encode("utf-32")):
        assert hub_http.read_json_body({"Content-Length": str(len(raw))}, io.BytesIO(raw)) == value


def test_both_doors_share_one_limit_and_rest_api_does_not_import_http_server():
    from vinc_mcp import http_server
    assert http_server.MAX_BODY is hub_http.MAX_BODY
    # 새 인터프리터에서 본다: 이 테스트 프로세스는 이미 http_server 를 실었다.
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONHOME", "PYTHONPATH")}
    probe = ("import sys, vinc_mcp.rest_api; "
             "sys.exit(1 if 'vinc_mcp.http_server' in sys.modules else 0)")
    result = subprocess.run([sys.executable, "-c", probe], cwd=Path(__file__).resolve().parents[2],
                            env=env, capture_output=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
