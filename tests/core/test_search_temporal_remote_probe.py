"""Synthetic transports only; the deployment probe never logs returned content."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from verify_search_temporal_remote import FIELDS, VerificationError, verify


def response(**overrides):
    return {"executed": True, "rows": [{"text": "synthetic private content",
             "id": "synthetic hidden identifier", "temporal": {**{k: None for k in FIELDS},
                "validity": "unknown"}}], "node_hits": [], **overrides}


def test_probe_uses_bounded_reads_and_only_emits_counts():
    from urllib.parse import parse_qs, urlsplit
    seen = []
    def get(path, key, timeout):
        seen.append(path)
        params = parse_qs(urlsplit(path).query)
        assert params["top_k"] == ["1"] and timeout == 30
        return response(mode=params.get("mode", [None])[0])
    result = verify("synthetic", get=get)
    assert len(seen) == 5 and result["search_temporal"] == "pass"
    assert "synthetic private" not in str(result) and "hidden identifier" not in str(result)


@pytest.mark.parametrize("bad", ["mode", "metadata", "current", "partial"])
def test_probe_rejects_silently_dropped_modes_or_metadata(bad):
    from urllib.parse import parse_qs, urlsplit
    def get(path, key, timeout):
        mode = parse_qs(urlsplit(path).query).get("mode", [None])[0]
        out = response(mode=mode)
        if bad == "mode": out["mode"] = None
        if bad == "metadata": out["rows"][0]["temporal"] = {}
        if bad == "current": out["rows"][0]["temporal"]["validity"] = "future"
        if bad == "partial": out["temporal_error"] = "synthetic failure"
        return out
    with pytest.raises(VerificationError):
        verify("synthetic", get=get)
