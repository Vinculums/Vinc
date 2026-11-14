"""Capacity changes must preserve the writer fence and recover failed trials."""
import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import hub_capacity_trial as trial
from gate_app_config import ACCOUNT_APP


_REAL_BASELINE = trial.BASELINE
_SYNTH_DIR = None


def setUpModule():
    # trial.run() 은 기준선 파일을 직접 읽는다. 이 파일의 테스트 동안만 1 CPU 출발점으로 바꾼다.
    global _SYNTH_DIR
    _SYNTH_DIR = tempfile.TemporaryDirectory()
    path = Path(_SYNTH_DIR.name) / "app-config-baseline.json"
    path.write_text(json.dumps(_one_cpu_baseline()), encoding="utf-8")
    trial.BASELINE = path


def tearDownModule():
    trial.BASELINE = _REAL_BASELINE
    if _SYNTH_DIR is not None:
        _SYNTH_DIR.cleanup()


def _one_cpu_baseline():
    """시험이 다루는 출발점: 1 CPU·2 GiB, 원격 인코더. 운영 기준선은 통합 뒤 2 CPU 라서
    그대로 쓰면 시험의 1→2 모델과 맞지 않는다(그때 시험은 사전 확인에서 거절한다)."""
    baseline = json.loads(_REAL_BASELINE.read_text(encoding="utf-8"))
    hub = baseline[trial.HUB_APP]
    hub["resources"] = {"cpu": 1.0, "ephemeralStorage": "4Gi", "memory": "2Gi"}
    hub["env_names"] = sorted(set(hub["env_names"]) | set(trial.LOCAL_ENCODER_ENV))
    return baseline


def state():
    return {"baseline": _one_cpu_baseline(), "images": {
        trial.HUB_APP: ["acr/image:fixed"], ACCOUNT_APP: ["acr/account:fixed"]}}


def app(cpu=1, revision="old"):
    return {"properties": {"latestRevisionName": revision,
        "latestReadyRevisionName": revision,
        "configuration": {"activeRevisionsMode": "Single"},
        "template": {"containers": [{"image": "acr/image:fixed", "resources": {
            "cpu": cpu, "memory": "2Gi" if cpu == 1 else "4Gi"}}]}}}


def revision(active, replicas=0, name="old"):
    return {"name": name, "properties": {"active": active, "replicas": replicas,
                                          "runningState": "Running"}}


class CapacitySafety(unittest.TestCase):
    def test_a_switch_never_stops_the_old_revision_first(self):
        """단일 writer 순서는 기동의 인계가 지킨다. 시험이 구 리비전을 먼저 내리면 플랫폼이 그것을
        다시 띄워 인계가 꼬인다."""
        events = []
        with patch.object(trial, "show", side_effect=[app(), app(2, "new")]), \
                patch.object(trial, "az", side_effect=lambda *args: events.append(args)), \
                patch.object(trial, "revisions", return_value=[revision(True, 1, "new")]), \
                patch.object(trial, "wait_ready", return_value="new"), \
                patch.object(trial, "verify_profile"):
            trial.switch(state(), 2)
        self.assertFalse(any("deactivate" in call for call in events), events)
        self.assertFalse(hasattr(trial, "stop_writer"))

    def test_image_change_is_not_silently_overwritten(self):
        changed = app()
        changed["properties"]["template"]["containers"][0]["image"] = "other"
        with patch.object(trial, "show", return_value=changed), \
                patch.object(trial, "az") as az:
            with self.assertRaises(RuntimeError):
                trial.switch(state(), 2)
        az.assert_not_called()

    def test_rollback_updates_resources_to_new_latest(self):
        events = []
        with patch.object(trial, "show", side_effect=[app(2), app(1, "new")]), \
                patch.object(trial, "az", side_effect=lambda *args: events.append(args)), \
                patch.object(trial, "revisions", return_value=[revision(True, 1, "new")]), \
                patch.object(trial, "wait_ready", return_value="new"), \
                patch.object(trial, "verify_profile"):
            trial.switch(state(), 1)
        update = events[0]
        self.assertEqual(update[:2], ("containerapp", "update"))
        self.assertIn("acr/image:fixed", update)
        self.assertEqual(update[-4:], ("--cpu", "1", "--memory", "2Gi"))

    def test_side_service_or_auth_drift_fails(self):
        saved = state()
        changed = trial.expected_profile(saved["baseline"], 2)
        changed[ACCOUNT_APP]["scale"]["minReplicas"] += 1
        with patch.object(trial, "public_state", return_value=(changed, saved["images"])):
            with self.assertRaises(RuntimeError):
                trial.verify_profile(saved, 2)

    def test_failed_resize_recovers_with_saved_non_secret_journal(self):
        saved = state()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            args = argparse.Namespace(state=str(path), expected_image="acr/image:fixed",
                                      expected_revision="old", hold_seconds=60)
            with patch.object(trial, "public_state", return_value=(saved["baseline"], saved["images"])), \
                    patch.object(trial, "show", return_value=app()), \
                    patch.object(trial, "verify_profile"), patch.object(trial, "wait_ready"), \
                    patch.object(trial, "switch", side_effect=RuntimeError("failed")), \
                    patch.object(trial, "restore") as restore:
                with self.assertRaises(RuntimeError):
                    trial.run(args)
            self.assertEqual(json.loads(path.read_text()), saved)
            restore.assert_called_once_with(saved)

    def test_stale_revision_does_not_create_recovery_journal_or_mutate(self):
        saved = state()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            args = argparse.Namespace(state=str(path), expected_image="acr/image:fixed",
                                      expected_revision="stale", hold_seconds=60)
            with patch.object(trial, "public_state", return_value=(saved["baseline"], saved["images"])), \
                    patch.object(trial, "show", return_value=app()), \
                    patch.object(trial, "switch") as switch, patch.object(trial, "restore") as restore:
                with self.assertRaises(RuntimeError):
                    trial.run(args)
            self.assertFalse(path.exists())
            switch.assert_not_called()
            restore.assert_not_called()

    def test_the_two_cpu_production_baseline_is_refused_before_any_change(self):
        """시험은 1→2→1 모델이다. 기준선이 이미 2 CPU 면 원복이 허브를 원격 인코더 없이 1 CPU 로
        내리므로, 아무것도 바꾸기 전에 거절해야 한다."""
        real = json.loads(_REAL_BASELINE.read_text(encoding="utf-8"))
        self.assertEqual(real[trial.HUB_APP]["resources"]["cpu"], 2.0)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            args = argparse.Namespace(state=str(path), expected_image="acr/image:fixed",
                                      expected_revision="old", hold_seconds=60)
            with patch.object(trial, "BASELINE", _REAL_BASELINE), \
                    patch.object(trial, "public_state", return_value=(real, state()["images"])), \
                    patch.object(trial, "show", return_value=app(2)), \
                    patch.object(trial, "switch") as switch, patch.object(trial, "restore") as restore:
                with self.assertRaises(RuntimeError):
                    trial.run(args)
            self.assertFalse(path.exists())
            switch.assert_not_called()
            restore.assert_not_called()

    def test_second_cleanup_does_not_restart_healthy_restored_hub(self):
        with patch.object(trial, "show", return_value=app()), \
                patch.object(trial, "revisions", return_value=[revision(True, 1)]), \
                patch.object(trial, "wait_ready"), patch.object(trial, "verify_profile"), \
                patch.object(trial, "az") as az, patch.object(trial, "switch") as switch:
            trial.restore(state())
        az.assert_not_called()
        switch.assert_not_called()


def local_state():
    saved = state()
    saved.update(local=True, encoder_env=[
        "VINC_EMBED_REMOTE=https://encode.example", "VINC_EMBED_ENCODE_TOKEN=secretref:encode-token"])
    return saved


class LocalEncoderTrial(unittest.TestCase):
    """로컬 인코더 시험: 2 CPU 구간에서만 원격 인코더 env 를 빼고, 원복 때 같은 정의로 되돌린다."""

    def _update_args(self, saved, cpu, current):
        events = []
        with patch.object(trial, "show", side_effect=[current, app(cpu, "new")]), \
                patch.object(trial, "az", side_effect=lambda *args: events.append(args)), \
                patch.object(trial, "revisions", return_value=[revision(True, 1, "new")]), \
                patch.object(trial, "wait_ready", return_value="new"), \
                patch.object(trial, "verify_profile"):
            trial.switch(saved, cpu)
        return events[0]

    def test_two_cpu_removes_the_remote_encoder_and_rollback_sets_it_back(self):
        up = self._update_args(local_state(), 2, app())
        i = up.index("--remove-env-vars")
        self.assertEqual(up[i + 1:i + 3], trial.LOCAL_ENCODER_ENV)
        down = self._update_args(local_state(), 1, app(2))
        i = down.index("--set-env-vars")
        self.assertEqual(list(down[i + 1:i + 3]), local_state()["encoder_env"])

    def test_resource_only_trial_does_not_touch_env(self):
        up = self._update_args(state(), 2, app())
        self.assertNotIn("--remove-env-vars", up)
        self.assertNotIn("--set-env-vars", up)

    def test_expected_profile_drops_only_the_encoder_env_and_only_at_two_cpu(self):
        base = state()["baseline"]
        two = trial.expected_profile(base, 2, local=True)[trial.HUB_APP]["env_names"]
        one = trial.expected_profile(base, 1, local=True)[trial.HUB_APP]["env_names"]
        self.assertEqual(one, base[trial.HUB_APP]["env_names"])
        self.assertEqual(set(base[trial.HUB_APP]["env_names"]) - set(two), set(trial.LOCAL_ENCODER_ENV))

    def test_encoder_env_keeps_the_secret_name_not_a_value(self):
        current = app()
        current["properties"]["template"]["containers"][0]["env"] = [
            {"name": "VINC_EMBED_REMOTE", "value": "https://encode.example"},
            {"name": "VINC_EMBED_ENCODE_TOKEN", "secretRef": "encode-token"},
            {"name": "OTHER", "value": "x"}]
        self.assertEqual(trial.encoder_env(current), local_state()["encoder_env"])

    def test_one_cpu_without_the_remote_env_is_not_yet_restored(self):
        with patch.object(trial, "show", return_value=app()), \
                patch.object(trial, "switch") as switch:
            trial.restore(local_state())
        switch.assert_called_once_with(local_state(), 1)


class SameAppsAsTheGate(unittest.TestCase):
    def test_public_state_covers_every_app_in_the_baseline(self):
        """기준선의 앱 목록과 시험이 보는 앱 목록이 같아야 사전 확인이 성립한다."""
        import json as _json
        baseline = _json.loads(trial.BASELINE.read_text(encoding="utf-8"))
        seen = []

        def fake_az(*args):
            if args[:2] == ("containerapp", "show"):
                seen.append(args[3])
                return {"properties": {"template": {"containers": [{"image": "img"}]}}}
            return {}

        with patch.object(trial, "az", side_effect=fake_az), \
                patch.object(trial, "project_app", side_effect=lambda data, auth: {}):
            actual, _ = trial.public_state()
        self.assertEqual(sorted(actual), sorted(baseline))
        self.assertEqual(sorted(seen), sorted(baseline))
