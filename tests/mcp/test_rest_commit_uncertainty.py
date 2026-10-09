"""Carry the sidecar's uncertain write through the real authenticated REST route."""
from test_rest_v1 import Server


def test_post_write_promotion_failure_forbids_rest_retry():
    server = Server()
    server.bridge.override = {"error": "OSError: persistent promotion failed",
                              "retryable": False, "write_may_have_applied": True,
                              "job_id": "job-1"}
    try:
        status, _headers, body = server.request(
            "PUT", "/v1/documents/retry-probe", body={"title": "Retry probe", "text": "synthetic"})
        assert status == 503
        assert body["error"]["details"]["retryable"] is False
        assert body["error"]["details"]["write_may_have_applied"] is True
    finally:
        server.close()
