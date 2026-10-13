"""Small authenticated reads; output contains counts and contract flags only."""
from __future__ import annotations

import json
import os
from urllib.parse import urlencode

from verify_layout_remote import VerificationError, read

FIELDS = {"created_at", "updated_at", "content_time", "valid_from", "valid_until",
          "superseded_by", "validity", "supersession", "supersedes", "claim_status", "conflicts"}


def verify(key: str, *, get=read) -> dict:
    if not key:
        raise VerificationError("VINC_GRAPH_READONLY_KEY is absent")
    checks = []
    for options in ({}, {"mode": "general"}, {"mode": "current"},
                    {"mode": "historical", "detail": "full"},
                    {"mode": "historical", "as_of": "2020", "known_at": "2025"}):
        result = get("/search?" + urlencode({"query": "vinc", "top_k": 1, **options}), key, 30)
        if options.get("mode") and result.get("mode") != options["mode"]:
            raise VerificationError("explicit temporal mode was not forwarded")
        items = (result.get("rows") or []) + (result.get("node_hits") or [])
        for row in items:
            temporal = row.get("temporal")
            if not isinstance(temporal, dict) or not FIELDS <= temporal.keys():
                raise VerificationError("search temporal metadata is incomplete")
            if options.get("mode") == "current" and temporal["validity"] in ("future", "historical"):
                raise VerificationError("current search returned outside current validity")
        if result.get("temporal_error"):
            raise VerificationError("temporal metadata read was partial")
        checks.append({"mode": options.get("mode", "legacy"), "items_checked": len(items),
                       "time_scope_requested": "as_of" in options,
                       "semantic_degraded": bool(result.get("vector_note")),
                       "contract": "pass"})
    if not any(c["items_checked"] for c in checks):
        raise VerificationError("no evidence metadata was available to verify")
    return {"search_temporal": "pass", "checks": checks, "content_logged": False}


if __name__ == "__main__":
    try:
        print(json.dumps(verify(os.environ.get("VINC_GRAPH_READONLY_KEY", "")), sort_keys=True))
    except VerificationError as exc:
        raise SystemExit(str(exc))
