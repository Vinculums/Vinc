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


def state():
    return {"baseline": json.loads(trial.BASELINE.read_text()), "images": {
        "vinc-hub": ["acr/image:fixed"], "vinc-encode": ["acr/image:fixed"],
        "vinc-account": ["acr/account:fixed"]}}


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
    def test_active_false_does_not_mean_writer_stopped(self):
        calls = [[revision(True, 1)], [revision(False, 1)], [revision(False, 0)]]
        with patch.object(trial, "revisions", side_effect=calls), \
                patch.object(trial, "az") as az, patch.object(trial.time, "sleep") as sleep:
            trial.stop_writer()
        self.assertEqual(az.call_count, 1)
        self.assertEqual(sleep.call_args_list[0].args, (10,))
        self.assertEqual(sleep.call_count, 2)  # 살아 있는 레플리카 대기 + 스냅샷 여유.

    def test_writer_timeout_forbids_update(self):
        with patch.object(trial, "show", return_value=app()), \
                patch.object(trial, "stop_writer", side_effect=RuntimeError("busy")), \
                patch.object(trial, "az") as az:
            with self.assertRaises(RuntimeError):
                trial.switch(state(), 2)
        az.assert_not_called()

    def test_image_change_is_not_silently_overwritten(self):
        changed = app()
        changed["properties"]["template"]["containers"][0]["image"] = "other"
        with patch.object(trial, "show", return_value=changed), \
                patch.object(trial, "stop_writer") as stop:
            with self.assertRaises(RuntimeError):
                trial.switch(state(), 2)
        stop.assert_not_called()

    def test_rollback_updates_resources_to_new_latest(self):
        events = []
        with patch.object(trial, "show", side_effect=[app(2), app(1, "new")]), \
                patch.object(trial, "stop_writer", side_effect=lambda: events.append("stopped")), \
                patch.object(trial, "az", side_effect=lambda *args: events.append(args)), \
                patch.object(trial, "revisions", return_value=[revision(True, 1, "new")]), \
                patch.object(trial, "wait_ready", return_value="new"), \
                patch.object(trial, "verify_profile"):
            trial.switch(state(), 1)
        self.assertEqual(events[0], "stopped")
        update = events[1]
        self.assertEqual(update[:2], ("containerapp", "update"))
        self.assertIn("acr/image:fixed", update)
        self.assertEqual(update[-4:], ("--cpu", "1", "--memory", "2Gi"))

    def test_side_service_or_auth_drift_fails(self):
        saved = state()
        changed = trial.expected_profile(saved["baseline"], 2)
        changed["vinc-encode"]["scale"]["minReplicas"] = 0
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
                patch.object(trial, "stop_writer"), \
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
        two = trial.expected_profile(base, 2, local=True)["vinc-hub"]["env_names"]
        one = trial.expected_profile(base, 1, local=True)["vinc-hub"]["env_names"]
        self.assertEqual(one, base["vinc-hub"]["env_names"])
        self.assertEqual(set(base["vinc-hub"]["env_names"]) - set(two), set(trial.LOCAL_ENCODER_ENV))

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
