# -*- coding: utf-8 -*-
"""SubagentStop 기록 훅 — 표본 입력으로 스크립트를 실제 프로세스로 돌린다.

라이브 세션의 하네스에서는 돌려 보지 않았다(설치는 머지 뒤 사람의 몫이다). 여기서 고정하는 것은
하네스와 맺는 계약 쪽이다: 어떤 입력에서도 종료 0, stdout 은 비어 있음(결정 출력이 없다), 로그 한 줄.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts import subagent_stop_record as hook  # noqa: E402

SCRIPT = Path(hook.__file__).resolve()
PR = "https://github.com/vincs-io/Vinc/pull/1290"


def _run(stdin: str, log: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", str(SCRIPT), "--log", str(log)], input=stdin,
                          capture_output=True, text=True, encoding="utf-8", timeout=30)


def _transcript(folder: Path, message: str) -> Path:
    """하네스 트랜스크립트의 꼴을 흉내 낸 세 줄: 도구 호출, 같은 요청의 다른 블록, 핸드백."""
    rows = [
        {"type": "user", "message": {"role": "user", "content": "task"}},
        {"type": "assistant", "requestId": "req_1", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]}},
        {"type": "assistant", "requestId": "req_1", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "thinking aloud"}]}},
        "not a json object",
        {"type": "assistant", "requestId": "req_2", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "SubagentHandback", "input": {"message": message}}]}},
    ]
    fp = folder / "agent-abc.jsonl"
    fp.write_text("\n".join(json.dumps(r) for r in rows) + "\n{broken\n", encoding="utf-8")
    return fp


class Record(unittest.TestCase):
    def test_a_handback_report_is_found_in_the_transcript_and_logged_as_one_line(self):
        with tempfile.TemporaryDirectory() as t:
            folder = Path(t)
            tr = _transcript(folder, f"PR: {PR}\nrow 1 ok, see {PR}")
            payload = {"session_id": "s1", "hook_event_name": "SubagentStop", "agent_id": "abc",
                       "agent_type": "agent-maker", "cwd": "/tmp", "stop_hook_active": False,
                       "agent_transcript_path": str(tr), "last_assistant_message": ""}
            log = folder / "logs" / "stops.jsonl"
            got = _run(json.dumps(payload), log)
            self.assertEqual((got.returncode, got.stdout), (0, ""))
            (line,) = log.read_text(encoding="utf-8").splitlines()
            row = json.loads(line)
            self.assertEqual(row["agent_type"], "agent-maker")
            self.assertEqual(row["report"], "handback")
            self.assertEqual(row["pull_requests"], [PR])
            self.assertEqual(row["model_calls"], 2)

    def test_without_a_transcript_the_last_message_is_read(self):
        with tempfile.TemporaryDirectory() as t:
            log = Path(t) / "stops.jsonl"
            payload = {"agent_type": "agent-checker", "agent_transcript_path": str(Path(t) / "gone.jsonl"),
                       "last_assistant_message": f"verdict fail on {PR}"}
            self.assertEqual(_run(json.dumps(payload), log).returncode, 0)
            row = json.loads(log.read_text(encoding="utf-8"))
            self.assertEqual((row["report"], row["pull_requests"], row["model_calls"]),
                             ("last_message", [PR], None))

    def test_no_report_records_none_and_no_url(self):
        row = hook.record({"agent_type": "agent-maker"})
        self.assertEqual((row["report"], row["pull_requests"]), ("none", []))


class FailsOpen(unittest.TestCase):
    """깨진 훅이 에이전트를 가두면 안 된다: 종료 2 와 stdout 의 결정만이 에이전트를 붙잡는다."""

    def test_bad_input_exits_zero_with_nothing_on_stdout(self):
        with tempfile.TemporaryDirectory() as t:
            log = Path(t) / "stops.jsonl"
            for stdin in ("{not json", "[1, 2]", ""):
                got = _run(stdin, log)
                self.assertEqual((got.returncode, got.stdout), (0, ""), stdin)
            self.assertIn("[subagent-stop]", _run("{not json", log).stderr)

    def test_an_unwritable_log_exits_zero(self):
        with tempfile.TemporaryDirectory() as t:
            blocker = Path(t) / "file"
            blocker.write_text("x", encoding="utf-8")
            got = _run(json.dumps({"agent_type": "agent-maker"}), blocker / "under-a-file.jsonl")
            self.assertEqual((got.returncode, got.stdout), (0, ""))
            self.assertIn("[subagent-stop]", got.stderr)

    def test_a_missing_log_argument_value_exits_zero(self):
        got = subprocess.run([sys.executable, "-I", str(SCRIPT), "--log"], input="{}",
                             capture_output=True, text=True, timeout=30)
        self.assertEqual((got.returncode, got.stdout), (0, ""))


if __name__ == "__main__":
    unittest.main()
