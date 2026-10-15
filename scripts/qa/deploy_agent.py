"""Host-side hub deployment gate. CLI operations only read GitHub.

Grant and Snapshot are host inputs, never authority supplied by model output.
This module is a gate, not an OS sandbox: the host must route mutations here.
"""
from __future__ import annotations

import argparse
import hashlib
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import subprocess
import time
from datetime import datetime
from typing import Callable

from scripts.verify_current_hub import REQUIRED

REPO = "Vinculums/Vinc"
SHA = re.compile(r"[0-9a-f]{40}")


def command(args):
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                          timeout=90, check=True).stdout


@dataclass(frozen=True)
class Grant:
    repository: str
    pr: int
    head: str
    expires_at: float
    reference: str
    action: str = "merge"


@dataclass(frozen=True)
class Snapshot:
    repository: str
    pr: int
    observed_at: float
    pull: dict
    checks: list
    active_runs: list


class GitHub:
    def __init__(self, run: Callable = command):
        self.run = run

    def get(self, path):
        return json.loads(self.run(["gh", "api", "--method", "GET", path]))

    def pages(self, path, field):
        result = []
        for page in range(1, 101):
            body = self.get(f"{path}{'&' if '?' in path else '?'}per_page=100&page={page}")
            rows = body[field]
            if not isinstance(rows, list):
                raise ValueError("invalid paginated response")
            result.extend(rows)
            if len(rows) < 100:
                return result
        raise ValueError("pagination limit reached; evidence incomplete")

    def snapshot(self, pr):
        started = time.time()
        pull = self.get(f"repos/{REPO}/pulls/{pr}")
        # GitHub, rather than a model-provided list, selects required checks.
        try:
            checks = json.loads(self.run([
                "gh", "pr", "checks", str(pr), "--repo", REPO, "--required",
                "--json", "name,bucket,state,link"]))
        except subprocess.CalledProcessError as exc:
            # gh uses nonzero for pending/failed checks too. Keep structured
            # evidence when available, but never treat absent checks as green.
            try:
                checks = json.loads(exc.stdout or "[]")
            except ValueError:
                raise ValueError("required check evidence unavailable") from None
            if not isinstance(checks, list):
                raise ValueError("invalid check evidence") from None
        active = []
        for status in ("queued", "in_progress", "waiting", "pending", "requested"):
            active.extend(self.pages(
                f"repos/{REPO}/actions/runs?branch=main&status={status}", "workflow_runs"))
        after = self.get(f"repos/{REPO}/pulls/{pr}")
        if pull.get("head", {}).get("sha") != after.get("head", {}).get("sha"):
            raise ValueError("PR changed during collection")
        return Snapshot(REPO, pr, started, after, checks, active)

    def attempt(self, run_id, attempt):
        base = f"repos/{REPO}/actions/runs/{run_id}/attempts/{attempt}"
        run = self.get(base)
        jobs = self.pages(base + "/jobs", "jobs")
        return {"run": run, "jobs": jobs, "observed_at": time.time()}

    def qualification(self, pr, deployment_run, verification_run):
        original = deployment(self.attempt(deployment_run, 1))
        evidence = self.attempt(verification_run, 1)
        run = evidence["run"]
        if (run.get("path") != ".github/workflows/hub-verify-current.yml"
                or run.get("status") != "completed" or run.get("conclusion") != "success"):
            raise ValueError("verification workflow did not succeed")
        jobs = [j for j in evidence["jobs"] if j.get("name") == "verify"
                and j.get("conclusion") == "success"]
        if len(jobs) != 1:
            raise ValueError("missing verification job")
        log = self.run(["gh", "run", "view", str(verification_run), "--repo", REPO,
                        "--attempt", "1", "--job", str(jobs[0]["id"]), "--log"])
        verification = parse_verification(log)
        verification.update(conclusion="success", url=run["html_url"],
                            completed_at=jobs[0]["completed_at"])
        pull = self.get(f"repos/{REPO}/pulls/{pr}")
        if pull.get("merged") is not True:
            raise ValueError("PR is not merged")
        merge_sha = pull["merge_commit_sha"]
        if datetime.fromisoformat(pull["merged_at"].replace("Z", "+00:00")) > datetime.fromisoformat(
                verification["completed_at"].replace("Z", "+00:00")):
            raise ValueError("verification predates merge")
        deployed_sha = verification["deployment_sha"]
        compared = self.get(f"repos/{REPO}/compare/{merge_sha}...{deployed_sha}")
        compared["head_sha"] = deployed_sha  # Exact SHA in the host request, not run.head_sha.
        return completion(original, verification, merge_sha, deployed_sha, compared)


def parse_verification(log):
    summaries = []
    stages = []
    for line in log.splitlines():
        # gh prefixes each line with job, step and timestamp; only runtime output
        # with our exact JSON shape is retained. Raw logs are never persisted.
        marker = line.find('{"deployment_sha":')
        if marker >= 0:
            try:
                summaries.append(json.loads(line[marker:]))
            except ValueError:
                pass
        marker = line.find("Verifying: ")
        if marker >= 0:
            stages.append(line[marker + len("Verifying: "):].strip())
    if len(summaries) != 1 or stages != list(REQUIRED):
        raise ValueError("verification log is incomplete or ambiguous")
    summary = summaries[0]
    if (not SHA.fullmatch(summary.get("deployment_sha", ""))
            or summary.get("verification") != "passed"
            or not isinstance(summary.get("warnings"), list)
            or any(w not in REQUIRED for w in summary["warnings"])):
        raise ValueError("verification summary has missing or unsuccessful stages")
    return {**summary, "required_stages": stages}


def preflight(snapshot, grant, producer_head, now=None):
    now = time.time() if now is None else now
    reasons = []
    p = snapshot.pull
    head = p.get("head", {}).get("sha", "")
    if snapshot.repository != REPO or p.get("base", {}).get("repo", {}).get("full_name") != REPO:
        reasons.append("repository mismatch")
    if p.get("number") != snapshot.pr or p.get("base", {}).get("ref") != "main":
        reasons.append("PR or base mismatch")
    if p.get("state") != "open" or p.get("draft") is not False or p.get("merged") is not False:
        reasons.append("PR is not open and ready")
    if p.get("mergeable") is not True or p.get("mergeable_state") != "clean":
        reasons.append("mergeability is not clean")
    if not SHA.fullmatch(head) or producer_head != head:
        reasons.append("producer proof does not match head")
    if not 0 <= now - snapshot.observed_at <= 60:
        reasons.append("stale observation")
    if not snapshot.checks or any(c.get("bucket") != "pass" for c in snapshot.checks):
        reasons.append("required checks are missing or unsuccessful")
    if snapshot.active_runs:
        reasons.append("main has unfinished workflow runs")
    if (grant is None or grant.action != "merge" or not grant.reference.strip()
            or grant.repository != REPO or grant.pr != snapshot.pr or grant.head != head
            or grant.expires_at <= now):
        reasons.append("missing or invalid host authorization")
    return {"allowed": not reasons, "reasons": reasons, "head": head}


def merge_once(github, pr, grant, producer_head, receipt_directory: Path):
    """Host API only. Exclusive receipt prevents replay after uncertain acknowledgement.

    The host owns receipt storage and grants. No admin bypass, automatic retry,
    tag, cloud update, publication or Linear mutation is exposed.
    """
    snapshot = github.snapshot(pr)
    verdict = preflight(snapshot, grant, producer_head)
    if not verdict["allowed"]:
        return verdict
    identity = json.dumps([grant.repository, grant.pr, grant.head, grant.action, grant.reference])
    receipt = receipt_directory / (hashlib.sha256(identity.encode()).hexdigest() + ".jsonl")
    # Atomic reservation is persisted before issuing the sole mutation.
    with receipt.open("x", encoding="utf-8") as stream:
        json.dump({"state": "attempt_reserved", "grant": asdict(grant),
                   "observed_at": snapshot.observed_at}, stream)
        stream.flush()
        import os
        os.fsync(stream.fileno())
    verdict = preflight(snapshot, grant, producer_head)
    if not verdict["allowed"]:
        return verdict
    try:
        github.run(["gh", "pr", "merge", str(pr), "--repo", REPO,
                    "--squash", "--match-head-commit", grant.head])
    except (subprocess.SubprocessError, OSError):
        # A failed acknowledgement is not permission to send another mutation.
        pass
    pull = github.get(f"repos/{REPO}/pulls/{pr}")
    result = {"state": "merged" if pull.get("merged") is True
              and pull.get("head", {}).get("sha") == grant.head else "uncertain",
            "merge_sha": pull.get("merge_commit_sha"), "url": pull.get("html_url"),
            "deployment_verified": False, "receipt": str(receipt)}
    with receipt.open("a", encoding="utf-8") as stream:
        stream.write("\n" + json.dumps(result) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return result


def deployment(evidence):
    """Evaluate a host-collected hub workflow attempt; never infer image from headSha."""
    run = evidence["run"]
    if run.get("path") != ".github/workflows/hub-deploy.yml":
        return {"state": "unsupported_workflow", "complete": False}
    jobs = [j for j in evidence["jobs"] if j.get("name") == "build-and-deploy"]
    if len(jobs) != 1:
        return {"state": "unknown", "complete": False}
    steps = {s["name"]: s.get("conclusion") for s in jobs[0].get("steps", [])}
    rolled_out = steps.get("Deploy to Container Apps") == "success"
    missing = [name for name in REQUIRED if steps.get(name) != "success"]
    verified = (not missing and run.get("status") == "completed"
                and run.get("conclusion") == "success" and jobs[0].get("conclusion") == "success")
    return {"state": "verification_passed" if verified else
            "rollout_verification_incomplete" if rolled_out else "not_verified",
            "complete": False, "verification_passed": verified,
            "rollout_observed": rolled_out, "unverified_stages": missing,
            "url": run.get("html_url"), "run_id": run.get("id"),
            "attempt": run.get("run_attempt"),
            "reason": "PR completion additionally requires deployed image SHA and commit containment"}


def completion(original, verification, merge_sha, deployed_sha, comparison):
    """Host must bind verification to the observed image and GitHub compare result.

    Accepts only structured facts produced by a trusted collector; model prose
    and workflow trigger SHA are never deployment evidence.
    """
    valid = (bool(SHA.fullmatch(merge_sha)) and bool(SHA.fullmatch(deployed_sha))
             and verification.get("deployment_sha") == deployed_sha
             and verification.get("verification") == "passed"
             and verification.get("required_stages") == list(REQUIRED)
             and verification.get("conclusion") == "success"
             and verification.get("warnings") == []
             and verification.get("url", "").startswith(f"https://github.com/{REPO}/actions/runs/")
             and comparison.get("base_commit", {}).get("sha") == merge_sha
             and comparison.get("merge_base_commit", {}).get("sha") == merge_sha
             and comparison.get("head_sha") == deployed_sha
             and comparison.get("status") in ("ahead", "identical"))
    return {"complete": valid, "state": ("recovered" if original.get("state") ==
            "rollout_verification_incomplete" and original.get("deployment_sha") == deployed_sha
            else "verified") if valid else "unverified",
            "original": original, "verification": verification,
            "reason": "all-stage qualification requires no warnings" if verification.get("warnings") else
            "exact deployed commit and containment required",
            "qualification_scope": "historical evidence, not current production state"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    pre = sub.add_parser("preflight")
    pre.add_argument("--pr", type=int, required=True)
    inspect = sub.add_parser("inspect")
    inspect.add_argument("--run", type=int, required=True)
    inspect.add_argument("--attempt", type=int, default=1)
    qualify = sub.add_parser("qualify")
    qualify.add_argument("--pr", type=int, required=True)
    qualify.add_argument("--deployment-run", type=int, required=True)
    qualify.add_argument("--verification-run", type=int, required=True)
    args = parser.parse_args()
    github = GitHub()
    try:
        if args.action == "preflight":
            snapshot = github.snapshot(args.pr)
            output = {"evidence": asdict(snapshot), "verdict": preflight(snapshot, None, "")}
        elif args.action == "inspect":
            evidence = github.attempt(args.run, args.attempt)
            output = {"evidence": evidence, "verdict": deployment(evidence)}
        else:
            output = {"verdict": github.qualification(args.pr, args.deployment_run, args.verification_run)}
    except (ValueError, KeyError, TypeError, subprocess.SubprocessError, OSError) as exc:
        # Do not print command output: logs can include credentials.
        output = {"complete": False, "state": "collection_failed", "error_type": type(exc).__name__}
    print(json.dumps(output, ensure_ascii=True, indent=2))
    return 0 if output.get("verdict", {}).get("complete") else 2


if __name__ == "__main__":
    raise SystemExit(main())
