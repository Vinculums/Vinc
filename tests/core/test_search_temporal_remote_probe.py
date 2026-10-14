"""Synthetic transports only; the deployment probe never logs returned content."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from verify_search_temporal_remote import FIELDS, VerificationError, verify


def response(**overrides):
    return {"kind": "search", "executed": True, "rows": [{"text": "synthetic private content",
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


def test_empty_account_miss_still_requires_real_metadata_evidence():
    from urllib.parse import parse_qs, urlsplit
    def get(path, key, timeout):
        mode = parse_qs(urlsplit(path).query).get("mode", [None])[0]
        return response(rows=[], mode=mode)
    with pytest.raises(VerificationError, match="no evidence"):
        verify("synthetic", get=get)
    result = verify("synthetic", get=get, mcp_key="synthetic",
                    mcp_get=lambda options, key, timeout: response(mode=options.get("mode")))
    assert len(result["checks"]) == 10
    assert result["checks"][0]["items_checked"] == 0
    assert result["checks"][5]["items_checked"] == 1


def test_empty_execution_error_is_not_accepted_as_a_miss():
    with pytest.raises(VerificationError):
        verify("synthetic", get=lambda *args: response(executed=False, rows=[], error="synthetic failure"))


def test_unexecuted_without_error_is_never_normalized_to_success():
    with pytest.raises(VerificationError, match="not executed"):
        verify("synthetic", get=lambda *args: response(executed=False, rows=[]),
               mcp_key="synthetic", mcp_get=lambda *args: response())


def test_failure_diagnostics_keep_only_stable_code():
    with pytest.raises(VerificationError) as exc:
        verify("synthetic", get=lambda *args: response(executed=False, rows=[],
               error="index_build_failed: synthetic private identifier"))
    assert "error_code=index_build_failed" in str(exc.value)
    assert "private identifier" not in str(exc.value)


def test_only_store_busy_is_retried_until_a_valid_result():
    from urllib.parse import parse_qs, urlsplit
    state = {"now": 0, "calls": 0}
    def get(path, key, timeout):
        state["calls"] += 1
        if state["calls"] <= 3:
            return response(executed=False, rows=[], error="store_busy")
        return response(mode=parse_qs(urlsplit(path).query).get("mode", [None])[0])
    def sleep(seconds):
        state["now"] += seconds
    result = verify("synthetic", get=get, clock=lambda: state["now"], sleep=sleep)
    assert state == {"now": 15, "calls": 8}
    assert result["checks"][0]["busy_retries"] == 3
    assert sum(check["busy_retries"] for check in result["checks"]) == 3


def test_persistent_store_busy_exhausts_budget_and_fails():
    state = {"now": 0, "calls": 0}
    def get(*args):
        state["calls"] += 1
        return response(executed=False, rows=[], error="store_busy")
    with pytest.raises(VerificationError, match="error_code=store_busy"):
        verify("synthetic", get=get, clock=lambda: state["now"],
               sleep=lambda seconds: state.__setitem__("now", state["now"] + seconds),
               wait_s=12)
    assert state == {"now": 12, "calls": 4}


def test_unrelated_execution_failure_is_not_retried():
    state = {"calls": 0}
    def get(*args):
        state["calls"] += 1
        return response(executed=False, rows=[], error="index_build_failed: private detail")
    with pytest.raises(VerificationError, match="error_code=index_build_failed"):
        verify("synthetic", get=get, sleep=lambda seconds: pytest.fail("unexpected retry"))
    assert state["calls"] == 1


def test_transient_account_gateway_error_requires_verified_result():
    from urllib.parse import parse_qs, urlsplit
    state = {"now": 0, "calls": 0}
    def get(path, key, timeout):
        state["calls"] += 1
        if state["calls"] <= 2:
            raise VerificationError("search read returned HTTP 502")
        mode = parse_qs(urlsplit(path).query).get("mode", [None])[0]
        return response(mode=mode)
    def sleep(seconds):
        state["now"] += seconds
    result = verify("synthetic", get=get, clock=lambda: state["now"], sleep=sleep)
    assert state == {"now": 10, "calls": 7}
    assert result["checks"][0]["gateway_retries"] == 2
    assert all(check["contract"] == "pass" for check in result["checks"])


def test_persistent_account_gateway_error_exhausts_budget_and_fails():
    state = {"now": 0, "calls": 0}
    def get(*args):
        state["calls"] += 1
        raise VerificationError("search read returned HTTP 502")
    with pytest.raises(VerificationError, match="HTTP 502"):
        verify("synthetic", get=get, clock=lambda: state["now"],
               sleep=lambda seconds: state.__setitem__("now", state["now"] + seconds),
               wait_s=12)
    assert state == {"now": 12, "calls": 4}


def test_unrelated_http_errors_fail_immediately_on_both_transports():
    from urllib.parse import parse_qs, urlsplit
    for error in ("search read returned HTTP 401", "search read returned HTTP 503"):
        def fail_read(*args):
            raise VerificationError(error)
        with pytest.raises(VerificationError, match=error):
            verify("synthetic", get=fail_read,
                   sleep=lambda _: pytest.fail("unexpected retry"))
    def good_read(path, key, timeout):
        return response(mode=parse_qs(urlsplit(path).query).get("mode", [None])[0])
    def bad_mcp(*args):
        raise VerificationError("search read returned HTTP 503")
    with pytest.raises(VerificationError, match="mcp/legacy: search read returned HTTP 503"):
        verify("synthetic", get=good_read, mcp_key="synthetic", mcp_get=bad_mcp,
            sleep=lambda _: pytest.fail("unexpected retry"))


@pytest.mark.parametrize("error,counter",[("search read returned HTTP 502","gateway_retries"),
                                        ("search read could not complete","transport_retries")])
def test_mcp_transport_recovery_requires_all_five_verified_contracts(error,counter):
    from urllib.parse import parse_qs, urlsplit
    state={"now":0,"mcp_calls":0}
    def get(path,*args):
        return response(mode=parse_qs(urlsplit(path).query).get("mode",[None])[0])
    def mcp(options,*args):
        state["mcp_calls"]+=1
        if state["mcp_calls"]<=2:
            raise VerificationError(error)
        return response(mode=options.get("mode"))
    out=verify("synthetic",get=get,mcp_key="synthetic",mcp_get=mcp,clock=lambda:state["now"],
               sleep=lambda seconds:state.__setitem__("now",state["now"]+seconds))
    assert state=={"now":10,"mcp_calls":7}
    assert out["checks"][5][counter]==2
    assert len(out["checks"])==10 and all(check["contract"]=="pass" for check in out["checks"])


def test_mcp_timeouts_exhaust_the_shared_deadline_and_do_not_become_empty_success():
    from urllib.parse import parse_qs, urlsplit
    state={"now":0,"mcp_calls":0}
    def get(path,*args):
        return response(mode=parse_qs(urlsplit(path).query).get("mode",[None])[0])
    def mcp(*args):
        state["mcp_calls"]+=1
        raise VerificationError("search read could not complete")
    with pytest.raises(VerificationError,match="mcp/legacy: search read could not complete"):
        verify("synthetic",get=get,mcp_key="synthetic",mcp_get=mcp,clock=lambda:state["now"],
               sleep=lambda seconds:state.__setitem__("now",state["now"]+seconds),wait_s=12)
    assert state=={"now":12,"mcp_calls":4}


def test_account_timeout_requires_a_subsequent_verified_result():
    from urllib.parse import parse_qs, urlsplit
    state = {"now": 0, "calls": 0}
    def get(path, key, timeout):
        state["calls"] += 1
        if state["calls"] == 1:
            raise VerificationError("search read could not complete")
        return response(mode=parse_qs(urlsplit(path).query).get("mode", [None])[0])
    result = verify("synthetic", get=get, clock=lambda: state["now"],
                    sleep=lambda seconds: state.__setitem__("now", state["now"] + seconds))
    assert state == {"now": 5, "calls": 6}
    assert result["checks"][0]["transport_retries"] == 1
    assert all(check["contract"] == "pass" for check in result["checks"])


def test_persistent_account_timeout_fails_at_the_existing_deadline():
    state = {"now": 0, "calls": 0}
    def get(*args):
        state["calls"] += 1
        raise VerificationError("search read could not complete")
    with pytest.raises(VerificationError, match="could not complete"):
        verify("synthetic", get=get, clock=lambda: state["now"],
               sleep=lambda seconds: state.__setitem__("now", state["now"] + seconds), wait_s=12)
    assert state == {"now": 12, "calls": 4}


@pytest.mark.parametrize("prefix",["TimeoutError","RemoteDisconnected","ConnectionResetError","ConnectionRefusedError"])
def test_nested_mcp_transport_error_is_classified_without_logging_private_details(monkeypatch,prefix):
    import verify_search_temporal_remote as probe
    monkeypatch.setattr(probe,"_json_read",lambda *args:{"result":{"isError":True,"content":[
        {"type":"text","text":prefix+": synthetic private detail"}]}})
    with pytest.raises(VerificationError,match="search read could not complete") as failure:
        probe.read_mcp({},"synthetic",30)
    assert "private detail" not in str(failure.value)


def test_unknown_mcp_tool_and_protocol_errors_still_fail_without_disclosing_messages(monkeypatch):
    import verify_search_temporal_remote as probe
    for envelope in [{"error":{"code":-32602,"message":"private invalid argument"}},
                     {"result":{"isError":True,"content":[{"type":"text","text":"RuntimeError: private failure"}]}}]:
        monkeypatch.setattr(probe,"_json_read",lambda *args:envelope)
        with pytest.raises(VerificationError,match="MCP search read returned an error") as failure:
            probe.read_mcp({},"synthetic",30)
        assert "private" not in str(failure.value)
