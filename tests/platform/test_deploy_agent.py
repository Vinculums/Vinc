import subprocess
import time

import pytest

from scripts.qa.deploy_agent import (
    REQUIRED, REPO, GitHub, Grant, Snapshot, completion, deployment, merge_once, preflight,
    parse_verification, parse_images,
)

HEAD = "a" * 40


def snapshot(**changes):
    values = dict(repository=REPO, pr=42, observed_at=time.time(),
                  pull={"number": 42, "base": {"ref": "main", "repo": {"full_name": REPO}},
                        "head": {"sha": HEAD}, "state": "open", "draft": False,
                        "merged": False, "mergeable": True, "mergeable_state": "clean"},
                  checks=[{"bucket": "pass"}], active_runs=[])
    values.update(changes)
    return Snapshot(**values)


def grant(**changes):
    values = dict(repository=REPO, pr=42, head=HEAD, expires_at=time.time() + 100,
                  reference="owner-approved-exact-head")
    values.update(changes)
    return Grant(**values)


def test_matching_host_grant_passes():
    assert preflight(snapshot(), grant(), HEAD)["allowed"]


@pytest.mark.parametrize("changes", [dict(repository="other/repo"), dict(pr=43),
    dict(head="b" * 40), dict(expires_at=0), dict(reference=""), dict(action="publish")])
def test_grant_cannot_widen_scope(changes):
    assert not preflight(snapshot(), grant(**changes), HEAD)["allowed"]


@pytest.mark.parametrize("changes", [dict(observed_at=0), dict(checks=[]),
    dict(checks=[{"bucket": "skipping"}]), dict(active_runs=[{"status": "waiting"}])])
def test_incomplete_or_stale_evidence_blocks(changes):
    assert not preflight(snapshot(**changes), grant(), HEAD)["allowed"]


def test_model_cannot_self_authorize():
    assert not preflight(snapshot(), None, HEAD)["allowed"]
    assert not preflight(snapshot(), grant(), "b" * 40)["allowed"]


def evidence():
    return {"run": {"id": 38541390682, "run_attempt": 1, "status": "completed",
                    "head_branch": "main", "event": "workflow_run",
                    "path": ".github/workflows/hub-deploy.yml", "conclusion": "failure"},
            "jobs": [{"name": "build-and-deploy", "conclusion": "failure", "steps": [
                {"name": "Deploy to Container Apps", "conclusion": "success"},
                *[{"name": name, "conclusion": "success"} for name in REQUIRED[:2]],
                {"name": REQUIRED[2], "conclusion": "failure"},
                *[{"name": name, "conclusion": "skipped"} for name in REQUIRED[3:]]]}]}


def test_historical_rollout_failure_is_not_completion():
    result = deployment(evidence())
    assert result["rollout_observed"]
    assert result["state"] == "rollout_verification_incomplete"
    assert not result["complete"]
    assert REQUIRED[3] in result["unverified_stages"]


def test_green_trigger_does_not_prove_deployed_commit():
    e = evidence()
    e["run"]["conclusion"] = e["jobs"][0]["conclusion"] = "success"
    for step in e["jobs"][0]["steps"]:
        step["conclusion"] = "success"
    assert deployment(e)["verification_passed"]
    assert not deployment(e)["complete"]


def test_same_workflow_name_on_untrusted_branch_is_not_deployment_evidence():
    e = evidence()
    e["run"]["head_branch"] = "unreviewed"
    assert deployment(e)["state"] == "unsupported_workflow"


def proof():
    return {"deployment_sha": HEAD, "verification": "passed", "required_stages": list(REQUIRED),
            "conclusion": "success", "warnings": [],
            "url": f"https://github.com/{REPO}/actions/runs/38545431994"}


def comparison():
    return {"base_commit": {"sha": HEAD}, "merge_base_commit": {"sha": HEAD},
            "head_sha": HEAD, "status": "identical"}


def test_recovery_preserves_original_failure():
    original = deployment(evidence())
    original["deployment_sha"] = HEAD
    result = completion(original, proof(), HEAD, HEAD, comparison())
    assert result["complete"] and result["state"] == "recovered"
    assert result["original"] == original


@pytest.mark.parametrize("changes", [dict(deployment_sha="b" * 40), dict(required_stages=[]),
    dict(conclusion="skipped"), dict(verification="unknown"), dict(url=""),
    dict(warnings=[REQUIRED[-2]])])
def test_unproven_recovery_blocks(changes):
    p = proof()
    p.update(changes)
    assert not completion(deployment(evidence()), p, HEAD, HEAD, comparison())["complete"]


def test_uncertain_merge_never_retries(tmp_path):
    class Host:
        calls = []
        def snapshot(self, pr):
            return snapshot()
        def run(self, args):
            self.calls.append(args)
            raise subprocess.TimeoutExpired(args, 90)
        def get(self, path):
            return {"merged": False}
    host = Host()
    assert merge_once(host, 42, grant(), HEAD, tmp_path)["state"] == "uncertain"
    with pytest.raises(FileExistsError):
        merge_once(host, 42, grant(), HEAD, tmp_path)
    assert len(host.calls) == 1
    assert "--match-head-commit" in host.calls[0]
    assert "--admin" not in host.calls[0]
    assert '"state": "uncertain"' in next(tmp_path.iterdir()).read_text()


def test_head_changed_during_collection_is_refused():
    import json
    calls = []
    def run(args):
        if args[1:3] == ["pr", "checks"]:
            return json.dumps([{"bucket": "pass"}])
        if "/pulls/" in args[-1]:
            calls.append(args[-1])
            return json.dumps({"head": {"sha": HEAD if len(calls) == 1 else "b" * 40}})
        return '{"workflow_runs": []}'
    with pytest.raises(ValueError, match="changed"):
        GitHub(run).snapshot(42)


def test_invalid_grant_never_reaches_mutation(tmp_path):
    class Host:
        def snapshot(self, pr):
            return snapshot()
        def run(self, args):
            pytest.fail("mutation must not run")
    assert not merge_once(Host(), 42, None, HEAD, tmp_path)["allowed"]
    assert list(tmp_path.iterdir()) == []


def test_pagination_reads_second_page():
    gh = GitHub()
    paths = []
    def get(path):
        paths.append(path)
        return {"jobs": [{}] * (100 if path.endswith("&page=1") else 1)}
    gh.get = get
    assert len(gh.pages("jobs", "jobs")) == 101
    assert len(paths) == 2


def test_historical_verify_success_preserves_mirror_warning():
    import json
    summary = {"deployment_sha": "260495a163531b7e2a3cbaedef16e899d1d13b22",
               "verification": "passed", "warnings": [REQUIRED[-2]]}
    log = "\n".join("Verifying: " + stage for stage in REQUIRED) + "\n" + json.dumps(summary)
    assert parse_verification(log)["warnings"] == [REQUIRED[-2]]
    with pytest.raises(ValueError):
        parse_verification(log + "\n" + json.dumps(summary))
    with pytest.raises(ValueError):
        parse_verification(log.replace("Verifying: " + REQUIRED[0], "omitted"))


def test_observed_images_bind_deployment_independently_of_trigger_head():
    log = f"timestamp hub    = vincacr.azurecr.io/vinc-hub:{HEAD}\n"
    log += f"timestamp encode = vincacr.azurecr.io/vinc-hub:{HEAD}\n"
    assert parse_images(log) == HEAD
    with pytest.raises(ValueError):
        parse_images(log + log)
    with pytest.raises(ValueError):
        parse_images(log.replace("encode = vincacr.azurecr.io/vinc-hub:" + HEAD,
                                 "encode = vincacr.azurecr.io/vinc-hub:" + "b" * 40))
