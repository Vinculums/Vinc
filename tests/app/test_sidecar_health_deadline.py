"""기동 확인은 HTTP 연결과 본문이 멈춰도 정해진 시간 안에 끝난다."""
import shutil
import subprocess
import unittest
from pathlib import Path


class SidecarHealthDeadlineTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required for the app fixture")
    def test_actual_client_with_unfinished_http_responses(self):
        fixture = Path(__file__).with_name("sidecar_health_deadline.mjs")
        result = subprocess.run([shutil.which("node"), "--test", str(fixture)],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
