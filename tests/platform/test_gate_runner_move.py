# -*- coding: utf-8 -*-
"""gate_runner_move 가 잡는 것과 안 잡는 것.

이 게이트의 값은 **좁게 정확한 것**에 있다. 직렬화하지 않는 워크플로까지 잡으면
사람이 게이트를 무시하는 법을 배우고, 그러면 게이트가 없는 것만 못하다. 그래서
경계 쪽 사례를 함께 고정한다.

진짜 저장소를 만들어 돌린다 — 이 스크립트가 `git show` 로 두 리비전을 읽는 것이
동작의 절반이라 그 부분을 흉내내면 검사할 것이 남지 않는다.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))

_SERIAL = """\
name: w
on: {push: {branches: [main]}}
concurrency:
  group: ${{ github.workflow }}
  cancel-in-progress: false
jobs:
  deploy:
    runs-on: %s
    steps: [{run: 'true'}]
"""

_EXPR = """\
name: w
on: {push: {branches: [main]}}
concurrency:
  group: ${{ github.workflow }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
jobs:
  deploy:
    runs-on: %s
    steps: [{run: 'true'}]
"""

_NO_CONCURRENCY = """\
name: w
on: {push: {branches: [main]}}
jobs:
  deploy:
    runs-on: %s
    steps: [{run: 'true'}]
"""


class _Repo:
    """두 커밋짜리 임시 저장소. before 를 커밋하고 after 로 고쳐 다시 커밋한다."""

    def __init__(self, before: str, after: str | None, path: str = ".github/workflows/w.yml"):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._run("git", "init", "-q")
        self._run("git", "config", "user.email", "t@t")
        self._run("git", "config", "user.name", "t")
        f = self.root / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(before, encoding="utf-8")
        self._run("git", "add", "-A")
        self._run("git", "commit", "-qm", "before")
        self.base = self._out("git", "rev-parse", "HEAD").strip()
        if after is None:
            f.unlink()
        else:
            f.write_text(after, encoding="utf-8")
        self._run("git", "add", "-A")
        self._run("git", "commit", "-qm", "after")
        self.head = self._out("git", "rev-parse", "HEAD").strip()

    def _run(self, *a):
        subprocess.run(a, cwd=self.root, check=True, capture_output=True)

    def _out(self, *a):
        return subprocess.run(a, cwd=self.root, capture_output=True,
                              text=True, encoding="utf-8").stdout

    def gate(self):
        # ⚠ `encoding="utf-8"` 을 **명시**한다. `text=True` 만 주면 로케일로 디코드하고
        # (윈도우 한국어는 cp949), 게이트가 한국어를 출력하는 순간 리더 스레드가
        # UnicodeDecodeError 로 죽어 `stdout` 이 None 이 된다. 그 결과 **잡아내는 경우만
        # 골라서** TypeError 로 깨진다 — 통과하는 경우는 출력이 비어 멀쩡하다.
        # 게이트 자체는 `-X utf8` 로 돌므로 내보내는 것은 언제나 UTF-8 이다.
        r = subprocess.run(
            [sys.executable, "-X", "utf8", str(_ROOT / "scripts" / "gate_runner_move.py"),
             self.base, self.head],
            cwd=self.root, capture_output=True, text=True, encoding="utf-8",
        )
        return r.returncode, (r.stdout or "") + (r.stderr or "")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self._tmp.cleanup()


class Catches(unittest.TestCase):
    def test_직렬화하는_워크플로의_러너가_바뀌면_잡는다(self):
        """Vinc#129 의 모양 그대로다."""
        with _Repo(_SERIAL % "[self-hosted, linux, vinc-local]",
                   _SERIAL % "ubuntu-latest") as r:
            rc, out = r.gate()
            self.assertEqual(rc, 1, out)
            self.assertIn("deploy", out)
            self.assertIn("큐를 비울 것", out)


class DoesNotCatch(unittest.TestCase):
    def test_표현식_cancel_in_progress_는_대상이_아니다(self):
        """ci.yml 의 모양. push 일 때 그룹 키에 sha 가 들어가 줄이 안 선다(#124)."""
        with _Repo(_EXPR % "[self-hosted, linux, vinc-local]",
                   _EXPR % "ubuntu-latest") as r:
            rc, out = r.gate()
            self.assertEqual(rc, 0, out)

    def test_concurrency_가_없으면_대상이_아니다(self):
        with _Repo(_NO_CONCURRENCY % "[self-hosted, linux, vinc-local]",
                   _NO_CONCURRENCY % "ubuntu-latest") as r:
            rc, out = r.gate()
            self.assertEqual(rc, 0, out)

    def test_러너가_그대로면_다른_곳이_바뀌어도_통과한다(self):
        """직렬화 워크플로를 고치는 것 자체는 막지 않는다. 러너 이동만 본다."""
        after = (_SERIAL % "ubuntu-latest").replace("steps: [{run: 'true'}]",
                                                    "steps: [{run: 'echo hi'}]")
        with _Repo(_SERIAL % "ubuntu-latest", after) as r:
            rc, out = r.gate()
            self.assertEqual(rc, 0, out)

    def test_새로_생긴_워크플로는_옮긴_것이_아니다(self):
        """before 에 없던 파일은 이동이 아니다 — 큐에 옛 런이 있을 수 없다."""
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            run = lambda *a: subprocess.run(a, cwd=root, check=True, capture_output=True)
            run("git", "init", "-q")
            run("git", "config", "user.email", "t@t")
            run("git", "config", "user.name", "t")
            (root / "seed.txt").write_text("x", encoding="utf-8")
            run("git", "add", "-A")
            run("git", "commit", "-qm", "seed")
            base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                                  capture_output=True, text=True).stdout.strip()
            f = root / ".github" / "workflows" / "w.yml"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(_SERIAL % "ubuntu-latest", encoding="utf-8")
            run("git", "add", "-A")
            run("git", "commit", "-qm", "add")
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                                  capture_output=True, text=True).stdout.strip()
            r = subprocess.run(
                [sys.executable, "-X", "utf8",
                 str(_ROOT / "scripts" / "gate_runner_move.py"), base, head],
                cwd=root, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class ThisRepo(unittest.TestCase):
    def test_직렬화하는_워크플로가_실제로_여덟이다(self):
        """게이트가 겨누는 대상이 이 리포에 실재하는지 고정한다. 늘거나 줄면
        이 테스트가 먼저 말한다 — 게이트가 조용히 아무것도 안 겨누게 두지 않는다.

        ⚠ **목록이 늘 때 이 테스트가 떨어지는 것은 고장이 아니라 설계다.** 새로
        직렬화하는 워크플로는 #129 형 사고의 새 후보이므로, 그것을 더하는 PR 에서
        사람이 한 번 보게 만든다. 늘리는 쪽이 여기 한 줄을 함께 고치면 된다.

        셋이 된 경위: `infra.yml`(#147)이 terraform apply 를 직렬화한다. 앞의 둘과
        달리 이쪽은 **상태를 바꾸는 쪽이 사람의 방아쇠 뒤에** 있어서, 큐가 막히면
        배포가 밀리는 것이 아니라 apply 가 안 나간다.

        넷이 된 경위: `pk-migrate.yml`(#136)은 자기 이름이 아니라 **`hub-deploy` 의
        그룹 키를 빌려 쓴다** — 같은 줄에 서야 이관 도중 배포가 끼어들지 못하기
        때문이다. 그래서 이 목록에서 유일하게 그룹 이름이 파일 이름과 다르고,
        막히는 방식도 다르다: 큐가 막히면 이관과 배포가 **서로** 밀린다.

        다섯째 자리였던 `encode-m3-probe.yml` 은 `vinc-encode` 앱을 내리면서 함께 지웠다(그
        앱의 토큰과 이미지를 대조하던 측정이라 앱 없이는 돌 수 없다). 그래서 아래 경위는
        여섯부터 이어지지만 목록에는 그 파일이 없다.

        여섯이 된 경위: `hub-store-recover.yml`(2026-09-11)은 pk-migrate 와 같은 모양으로
        `hub-deploy` 의 그룹 키를 빌려 쓴다 — 허브를 다른 스토어 파일로 갈아타는 도중에
        배포가 끼어들면 두 리비전이 겹치기 때문이다. 컨테이너 exec·볼륨 복사·env 교체를
        배포 신원으로 돌리는 복구 도구이고, dispatch 로만 돈다.

        일곱이 된 경위: `hub-store-rebuild.yml`(2026-09-27)도 같은 이유로 `hub-deploy` 의
        그룹 키를 빌려 쓴다 — 허브를 내리고 재구축본으로 갈아타는 도중에 배포가 끼어들면
        내려 둔 허브가 다시 뜨고 두 리비전이 겹친다. dispatch 로만 돈다.

        여덟이 된 경위: `integrations-publish.yml` 은 같은 ref 의 패키지 게시를
        직렬화한다. 업로드 중인 런을 취소하면 wheel 과 sdist 중 한쪽만 남을 수 있어
        `cancel-in-progress: false` 로 뒤의 런을 대기시킨다.

        아홉이 된 경위: `hub-data-repair.yml`(2026-09-30)은 `hub-store-rebuild.yml` 과 같은
        모양이고 같은 이유로 `hub-deploy` 의 그룹 키를 빌려 쓴다 — 허브를 내리고 수리본으로
        갈아타는 도중에 배포가 끼어들면 내려 둔 허브가 다시 뜬다. dispatch 로만 돈다.

        열하나가 된 경위: `prefetch-bench.yml`(2026-10-07, #676)은 제품 상태를 바꾸지 않고
        라이브 허브의 지연을 잰다. 직렬화하는 이유는
        **측정끼리 겹치면 서로를 오염시키기 때문**이다: 1코어 허브에 벤치 둘이 동시에
        붙으면 둘 다 느려진다. 큐가 막혀도 밀리는 것은 측정뿐이고, dispatch 로만 돈다.
        근거: [[vinc.plan.vin397-prefetch-half-latency-2026-10-07]]

        열둘이 된 경위: `layout-deploy.yml`(2026-10-12)은 `vinc-layout` 하나의 이미지를 바꾼다.
        같은 앱에 `containerapp update` 둘이 겹치면 뒤의 것이 앞의 것을 덮고 끝의 이미지 대조가
        엉뚱한 런에서 실패한다. 스토어를 안 물어 허브 그룹 키는 빌리지 않는다.
        근거: [[decision:time-branch-layout-is-computed-on-the-hub-per-topic-2026-10-12]]

        `azure-ops.yml` 은 컨테이너 앱의 현재 리비전을 재시작한다. 허브를 고르면 `hub-deploy` 의
        그룹 키를 빌린다: 배포·수리 도중의 재시작은 내려 둔 허브를 다시 띄우거나 두 리비전을
        겹치게 한다. 재시작을 중간에 끊지 않도록 취소하지 않는다. dispatch 로만 돈다.
        근거: [[vinc.ops.azure-identity-split-2026-08-30]]"""
        import yaml
        serial = []
        for p in sorted((_ROOT / ".github" / "workflows").glob("*.yml")):
            doc = yaml.safe_load(p.read_text(encoding="utf-8"))
            c = (doc or {}).get("concurrency")
            if isinstance(c, dict) and c.get("cancel-in-progress") is False:
                serial.append(p.name)
        self.assertEqual(serial, ["azure-ops.yml", "hub-capacity-trial.yml", "hub-data-repair.yml", "hub-deploy.yml", "hub-durability-latency.yml",
                                  "hub-handoff-rehearsal.yml", "hub-relevance-measure.yml", "hub-store-rebuild.yml",
                                  "hub-store-recover.yml", "hub-verify-current.yml", "infra.yml", "integrations-publish.yml", "layout-deploy.yml", "pk-migrate.yml",
                                  "prefetch-bench.yml", "release.yml", "topic-copy-rehearsal.yml", "topic-snapshot-audit.yml"], serial)


if __name__ == "__main__":
    unittest.main()


class FailsClosed(unittest.TestCase):
    """읽을 수 없을 때 통과시키지 않는가.

    이 게이트는 `git show <rev>:<path>` 로 두 리비전을 읽는다. 예전에는 그 실패가
    '그 리비전에 파일이 없다'와 **같은 값**(None)이라 그냥 건너뛰어졌고, 그러면
    검사한 워크플로가 0건인데 결과는 PASS 였다. 얕은 체크아웃 하나면 그 상태가 된다.

    이 리포가 같은 모양을 세 번 만난 뒤에 붙인 검사다 — 값이 빈 시크릿이 스킵 분기를
    타고, 테스트가 호환 분기를 타고, 이행 검증이 앞선 거절 때문에 대상 표에 닿지 못한
    채, 셋 다 초록이었다."""

    def test_알_수_없는_base_는_통과가_아니라_실패다(self):
        """러너가 실제로 옮겨지는 PR 을 만들고, base 만 못 읽게 한다.
        고쳐지기 전에는 rc=0(통과)이었다 — 잡아야 할 것을 눈앞에 두고."""
        with _Repo(_SERIAL % "[self-hosted, linux, vinc-local]",
                   _SERIAL % "ubuntu-latest") as r:
            rc, out = r.gate()
            self.assertEqual(rc, 1, "전제 확인: 정상 체크아웃에서는 잡아낸다")

            missing = "0" * 40          # 이 저장소에 없는 커밋
            proc = subprocess.run(
                [sys.executable, "-X", "utf8",
                 str(_ROOT / "scripts" / "gate_runner_move.py"), missing, r.head],
                cwd=r.root, capture_output=True, text=True, encoding="utf-8",
            )
            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
            self.assertIn("못 읽었다", proc.stdout + proc.stderr)
            self.assertIn("fetch-depth", proc.stdout + proc.stderr,
                          "가장 흔한 원인과 처방을 함께 말해야 한다")


class QueueVerification(unittest.TestCase):
    def test_queue_read_covers_all_nonterminal_states(self):
        import gate_runner_move as gate
        results = [subprocess.CompletedProcess([], 0, output, "")
                   for output in ("12\n", "13\n", "", "12\n", "")]
        with patch.object(gate.subprocess, "run", side_effect=results) as run:
            self.assertEqual(gate.active_runs("Vinculums/Vinc", "release.yml"), [12, 13])
        self.assertEqual(run.call_count, 5)
        self.assertTrue(all("--paginate" in call.args[0] for call in run.call_args_list))

    def test_failed_api_read_does_not_mean_empty_queue(self):
        import gate_runner_move as gate
        with patch.object(gate.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "")):
            with self.assertRaises(gate.Unreadable):
                gate.active_runs("Vinculums/Vinc", "release.yml")

    def test_malformed_api_response_is_not_accepted(self):
        import gate_runner_move as gate
        with patch.object(gate.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "bad", "")):
            with self.assertRaises(gate.Unreadable):
                gate.active_runs("Vinculums/Vinc", "release.yml")
