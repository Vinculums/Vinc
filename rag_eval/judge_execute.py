"""Bounded executor for the predicate judge comparison: sends each prepared request once.

    python -m rag_eval.judge_execute --root . --contract <contract.json> --directory <prepared dir> \\
        --set calib --journal <dir>/journal-calib.jsonl --ledger <run>/request-ledger.jsonl

What it guarantees, and what the offline importer cannot check for itself:
- a request is counted in the ledger before it leaves, and nothing is sent once the contract's cap is reached;
- no request is sent twice: a request already in the journal or the ledger is never repeated, not even after a crash;
- the first failure stops the run: an HTTP status other than 200, no answer, or an answer the importer would refuse;
- the API key is read from the environment and is written nowhere.

Preparation protocol: [[vinc.plan.predicate-judge-comparison-preparation-2026-10-10]].
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import judge_compare as J

KEY_ENV = {"jev": "TYPESAFE_API_KEY", "openai_decisions": "OPENAI_API_KEY"}
HOSTS = {"jev": "api.typesafe.ai", "openai_decisions": "api.openai.com"}
OK, REFUSED, STOPPED, CAP_REACHED = 0, 2, 3, 4


class Refused(Exception):
    """Nothing was sent: a precondition does not hold."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # 리다이렉트를 따라가면 Authorization 헤더가 계약에 없는 호스트로 갈 수 있다.
    def redirect_request(self, *args, **kwargs):
        return None


def http_send(url: str, key: str, body: dict, timeout: float):
    """(HTTP status, parsed JSON or None). 0 means no HTTP answer at all. Error bodies are not kept."""
    request = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST",
                                     headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=timeout) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, None
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, None
    try:
        return status, json.loads(raw.decode("utf-8"), object_pairs_hook=J.unique_object)
    except ValueError:
        return status, None


def _append(path: Path, row: dict) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _rows(path: Path) -> list[dict]:
    return J.read_journal(path) if path.exists() else []


def _now() -> datetime:
    return datetime.now(timezone.utc)


def run(*, root: Path, contract_path: Path, directory: Path, set_name: str, journal: Path, ledger: Path, key: str,
        send=http_send, max_requests: int | None = None, limit: int = 0, min_interval: float = 1.0,
        timeout: float = 60.0, sleep=time.sleep, dry_run: bool = False, log=print) -> int:
    manifest, sets = J.prepared(directory, root, contract_path)
    contract, provider, pins = manifest["contract"], manifest["provider"], manifest["provenance"]
    execution = contract.get("execution") or {}
    if contract.get("live_calls_authorized") is not True or type(execution.get("max_requests_total")) is not int:
        raise Refused("this contract does not authorize live calls")
    cap = execution["max_requests_total"] if max_requests is None else min(execution["max_requests_total"], max_requests)
    if set_name not in sets:
        raise Refused("unknown set for this contract: " + set_name)
    url = urllib.parse.urlsplit(pins["endpoint"])
    if url.scheme != "https" or url.hostname != HOSTS[provider]:
        raise Refused("endpoint is not the provider's registered https host")
    if set_name != "calib":
        if not (directory / "calibration-frozen.json").exists():
            raise Refused("calibration is not frozen yet, so no measurement request may leave")
        J.verified_calibration(directory, manifest)  # 보정이 동결되고 그대로일 때만 측정 요청을 보낸다
    path = directory / f"requests-{set_name}.jsonl"
    if not path.exists():
        raise Refused("requests for this set have not been released")
    requests = J.read_journal(path)
    if requests != J.requests_for(provider, pins["model"], set_name, sets[set_name]):
        raise Refused("request file differs from the frozen inputs")

    receipts = {r["request_id"]: r for r in _rows(journal)}
    if any("error" in r for r in receipts.values()):
        raise Refused("the journal holds a failed attempt; recovery needs its own decision")
    counted = _rows(ledger)
    mine = {e["request_id"] for e in counted if e["provider"] == provider and e["set"] == set_name}
    if mine - set(receipts):
        raise Refused("the ledger counts a request with no receipt; its outcome is unknown and it is not sent again")
    if set(receipts) - mine:
        raise Refused("the journal holds a receipt the ledger does not count")
    todo = [r for r in requests if r["request_id"] not in receipts]
    if limit:
        todo = todo[:limit]
    log(f"{provider} {set_name}: {len(receipts)} done, {len(todo)} to send, ledger {len(counted)} of cap {cap}")
    if dry_run:
        return OK
    if not key:
        raise Refused("no API key in the environment variable " + KEY_ENV[provider])

    sent = len(counted)
    for index, request in enumerate(todo):
        if J.digest(request["body"]) != request["body_sha256"]:
            raise Refused("request body hash mismatch")
        if sent >= cap:
            log(f"cap of {cap} requests reached; nothing more is sent")
            return CAP_REACHED
        if index:
            sleep(min_interval)
        started = _now()
        _append(ledger, {"at": started.isoformat(), "provider": provider, "set": set_name,
                         "request_id": request["request_id"], "body_sha256": request["body_sha256"]})
        sent += 1
        clock = time.perf_counter()
        status, response = send(pins["endpoint"], key, request["body"], timeout)
        elapsed = round(time.perf_counter() - clock, 3)
        receipt = {"request_id": request["request_id"], "attempt": 1, "body_sha256": request["body_sha256"],
                   "started_at": started.isoformat(), "completed_at": _now().isoformat(), "elapsed_s": elapsed}
        if status != 200 or response is None:
            _append(journal, {**receipt, "error": {"http_status": status}})
            log(f"stopped at {request['request_id']}: HTTP status {status}")
            return STOPPED
        try:
            J.probabilities(provider, response, request["predicates"], pins["model"])
        except (ValueError, KeyError, TypeError, AttributeError) as problem:
            _append(journal, {**receipt, "response": response, "error": {"invalid_response": str(problem)[:200]}})
            log(f"stopped at {request['request_id']}: the answer would be refused on import ({problem})")
            return STOPPED
        _append(journal, {**receipt, "response": response})
    log(f"{provider} {set_name}: sent {len(todo)}, ledger now {sent} of cap {cap}")
    return OK


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--contract", type=Path, required=True)
    ap.add_argument("--directory", type=Path, required=True)
    ap.add_argument("--set", dest="set_name", required=True)
    ap.add_argument("--journal", type=Path, required=True)
    ap.add_argument("--ledger", type=Path, required=True, help="one file shared by both providers and every set")
    ap.add_argument("--max-requests", type=int, help="can only lower the contract's cap")
    ap.add_argument("--limit", type=int, default=0, help="send at most this many now; a later run continues")
    ap.add_argument("--min-interval", type=float, default=1.0, help="seconds between requests")
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--dry-run", action="store_true", help="check everything and print the counts; send nothing")
    args = ap.parse_args(argv)
    manifest = json.loads((args.directory / "manifest.json").read_text(encoding="utf-8"))
    try:
        return run(root=args.root, contract_path=args.contract, directory=args.directory, set_name=args.set_name,
                   journal=args.journal, ledger=args.ledger, key=os.environ.get(KEY_ENV[manifest["provider"]], ""),
                   max_requests=args.max_requests, limit=args.limit, min_interval=args.min_interval,
                   timeout=args.timeout, dry_run=args.dry_run)
    except (Refused, ValueError) as problem:
        print("refused: " + str(problem), file=sys.stderr)
        return REFUSED


if __name__ == "__main__":
    raise SystemExit(main())
