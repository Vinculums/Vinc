# -*- coding: utf-8 -*-
"""컨테이너 종료 경로 — `packaging/docker-entrypoint.sh`.

**왜 셸 스크립트까지 테스트하나.** 2026-08-07 실측: 임베딩 백로그를 109 → 0 으로 다
비운 직후, 배포 한 번에 **문서 66건의 `needs_embed` 해제가 사라졌다.**

사슬은 이렇다. 엔트리포인트의 마지막 줄이 `exec python … mcp serve-http` 였는데,
`exec` 는 셸 프로세스를 통째로 대체하므로 **그 순간 위에 걸어 둔 trap 이 사라진다.**
그러면 컨테이너 종료 시 SIGTERM 이 PID 1(=MCP)에게만 가고 사이드카는 아무 신호도 못
받은 채 컨테이너와 함께 SIGKILL 로 죽는다 → `app.close()` 를 못 돌아 **WAL 이
체크포인트되지 않고** → 다음 기동에서 `VINC_WAL_REPLAY_TOLERANT=1` 이 그 미완결
꼬리를 버린다(관용 옵션은 복구가 아니라 폐기다).

임베딩만의 문제가 아니다 — 체크포인트 전의 **모든 쓰기**가 대상이고, 반복된 강제
종료는 같은 날 두 번 겪은 FTS 인덱스 손상과도 같은 자리다.

파이썬 쪽은 이미 옳다(`api/server.py` `serve()`: SIGTERM → stop → finally
`app.close()`). 신호를 전달할 사람이 없었을 뿐이라 계약은 셸에 있고, 그래서 검사도
여기 있다. 진짜 스크립트를 돌리되 `python`·`curl` 만 스텁으로 가로챈다.

⚠ 이 검사가 무엇도 안 잡는 상태로 남지 않게, 구현 전 코드에서 **실제로 실패하는지**
확인했다(구 `exec` 버전 = FAIL · 현 버전 = PASS).
"""
from __future__ import annotations

import os
import pathlib
import shutil

import subprocess
import time
import unittest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_ENTRY = _ROOT / "packaging" / "docker-entrypoint.sh"

_CURL = '#!/bin/sh\necho \'{"status": "ok"}\'\n'

# 사이드카 대역은 **닫는 데 시간이 걸린다**(진짜 store.close() 가 그렇다). 셸이
# 기다리지 않으면 마커가 안 남는다 — 그 기다림이 이 검사의 전부다.
_PY = """#!/bin/sh
OUT="{out}"
case "$*" in
  *"api.cli serve"*)
      trap 'sleep 2; echo closed > "$OUT/sidecar-closed"; exit 0' TERM
      echo started > "$OUT/sidecar-started"
      while true; do sleep 0.2; done ;;
  *"mcp serve-http"*)
      echo started > "$OUT/mcp-started"
      trap 'exit 0' TERM
      while true; do sleep 0.2; done ;;
  *embed_worker*)
      trap 'exit 0' TERM
      while true; do sleep 0.2; done ;;
  *) exit 0 ;;
esac
"""

# 엔트리포인트를 띄우고 → MCP 기동을 기다리고 → **셸에서** TERM 을 보내고 →
# 사이드카가 닫혔는지 본다. 종료코드가 곧 판정이다.
_DRIVER = """
sh "{entry}" > "{out}/log" 2>&1 &
ENTRY=$!
i=0
while [ "$i" -lt 150 ] && [ ! -f "{out}/mcp-started" ]; do i=$((i+1)); sleep 0.2; done
if [ ! -f "{out}/mcp-started" ]; then echo "MCP 미기동"; cat "{out}/log"; exit 2; fi
kill -TERM "$ENTRY" 2>/dev/null
i=0
while [ "$i" -lt 150 ] && kill -0 "$ENTRY" 2>/dev/null; do i=$((i+1)); sleep 0.2; done
cat "{out}/log"
[ -f "{out}/sidecar-closed" ] || exit 1
exit 0
"""


@unittest.skipUnless(shutil.which("sh"), "POSIX sh 없음")
class EntrypointShutdownTests(unittest.TestCase):
    """계약: **SIGTERM 이 사이드카까지 가고, 셸이 사이드카가 닫을 때까지 기다린다.**"""

    def setUp(self):
        import tempfile
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="vinc-entry-"))
        self.out = self.tmp / "out"
        self.bin = self.tmp / "bin"
        self.out.mkdir()
        self.bin.mkdir()
        (self.bin / "curl").write_text(_CURL, encoding="utf-8")
        (self.bin / "python").write_text(
            _PY.format(out=self.out.as_posix()), encoding="utf-8")
        for f in ("curl", "python"):
            os.chmod(self.bin / f, 0o755)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _wait_for(self, path: pathlib.Path, timeout: float) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if path.exists():
                return True
            time.sleep(0.1)
        return False

    def test_사이드카가_SIGTERM_을_받고_셸이_닫힐_때까지_기다린다(self):
        """⚠ 신호는 **셸 안에서** 보낸다. Windows 에서 파이썬의 `send_signal(SIGTERM)`
        은 POSIX 신호가 아니라 `TerminateProcess` 라 trap 이 아예 안 돌고, 그러면 이
        검사가 **고쳐진 코드에서도 실패한다**(환경을 결함으로 오진하는 꼴). 드라이버를
        `sh` 에 맡기면 Linux CI 와 로컬 Git Bash 가 같은 경로를 밟는다."""
        env = dict(os.environ)
        env.update({
            "PATH": str(self.bin) + os.pathsep + env.get("PATH", ""),
            "VINC_SIDECAR_TOKEN": "t",
            "VINC_DATA_DIR": (self.out / "db").as_posix(),
            "VINC_EMBED": "off",
            "VINC_MCP_STATE_DIR": (self.out / "state").as_posix(),
            "VINC_MCP_STATE_SNAPSHOT": (self.out / "snap" / "oauth.db").as_posix(),
            # 🔴 **pidfile 을 이 검사의 것으로 가둔다.** 기본값은 `/tmp/vinc-*.pid`
            # 고정 경로다 — 컨테이너 안에서는 한 벌뿐이라 맞지만, **한 기계에서 이
            # 스크립트를 여러 번 돌리면**(이 파일이 그렇게 하고, CI 는 `core` 와
            # `core-pkns` 두 잡을 같은 러너에서 동시에 돌린다) 서로의 pidfile 을
            # 덮어쓴다. 그러면 `shutdown()` 이 **남의 사이드카**를 죽이고 "정상 종료"
            # 를 찍은 뒤, 정작 자기 대역은 TERM 을 못 받아 마커를 안 남긴다.
            # 그 모양으로 2026-09-10 에 이 파일이 두 번 붉었다(한 번은 재실행에서
            # 통과해 흔들림으로 읽혔다). 엔트리포인트가 이 env 를 연 이유가 그것이다.
            "VINC_SIDECAR_PIDFILE": (self.out / "sidecar.pid").as_posix(),
            "VINC_EMBED_PIDFILE": (self.out / "embed.pid").as_posix(),
            "VINC_NODE_MIRROR_PIDFILE": (self.out / "mirror.pid").as_posix(),
        })
        env.pop("VINC_ROLE", None)          # 허브 역할로 돈다(encode 는 즉시 exec)
        driver = _DRIVER.format(entry=_ENTRY.as_posix(), out=self.out.as_posix())
        proc = subprocess.run(["sh", "-c", driver], cwd=str(_ROOT), env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, timeout=90)
        self.assertEqual(
            proc.returncode, 0,
            "사이드카가 SIGTERM 을 못 받았거나 셸이 안 기다렸다 — WAL 이 미완결로 "
            "남아 다음 기동에서 최근 쓰기가 사라진다(2026-08-07 실측 66문서).\n"
            + (proc.stdout or "")[-1500:])
        self.assertTrue((self.out / "sidecar-closed").exists())

    def test_도는_미러_재구축이_종료를_붙들지_않는다(self):
        """재구축은 배포마다 배경으로 돌고 분 단위다. 그 요청이 닫으려는 사이드카를
        놓지 않으면 `shutdown()` 의 20초 대기를 넘기고, Container Apps 유예(30초)도
        넘겨 SIGKILL 이 된다 — 그 끝이 체크포인트 안 된 WAL 이고, 위 검사가 지키는
        바로 그 사슬이다. 그래서 종료는 재구축부터 걷어내야 한다.

        ⚠ 서브셸이 아니라 **curl 을 죽여야** 한다. 서브셸만 죽이면 curl 이 고아로 남아
        연결을 그대로 쥔다 — 그래서 pid 를 파일로 남긴다."""
        (self.bin / "curl").write_text(
            "#!/bin/sh\n"
            "case \"$*\" in\n"
            "  *node-mirror/rebuild*)\n"
            f"      trap 'echo killed > \"{self.out.as_posix()}/mirror-killed\"; exit 0' TERM\n"
            "      i=0; while [ \"$i\" -lt 600 ]; do i=$((i+1)); sleep 0.2; done ;;\n"
            "  *) echo '{\"status\": \"ok\"}' ;;\n"
            "esac\n", encoding="utf-8")
        os.chmod(self.bin / "curl", 0o755)

        env = dict(os.environ)
        env.update({
            "PATH": str(self.bin) + os.pathsep + env.get("PATH", ""),
            "VINC_SIDECAR_TOKEN": "t",
            "VINC_DATA_DIR": (self.out / "db").as_posix(),
            "VINC_EMBED": "off",
            "VINC_MCP_STATE_DIR": (self.out / "state").as_posix(),
            "VINC_MCP_STATE_SNAPSHOT": (self.out / "snap" / "oauth.db").as_posix(),
            # 🔴 **pidfile 을 이 검사의 것으로 가둔다.** 기본값은 `/tmp/vinc-*.pid`
            # 고정 경로다 — 컨테이너 안에서는 한 벌뿐이라 맞지만, **한 기계에서 이
            # 스크립트를 여러 번 돌리면**(이 파일이 그렇게 하고, CI 는 `core` 와
            # `core-pkns` 두 잡을 같은 러너에서 동시에 돌린다) 서로의 pidfile 을
            # 덮어쓴다. 그러면 `shutdown()` 이 **남의 사이드카**를 죽이고 "정상 종료"
            # 를 찍은 뒤, 정작 자기 대역은 TERM 을 못 받아 마커를 안 남긴다.
            # 그 모양으로 2026-09-10 에 이 파일이 두 번 붉었다(한 번은 재실행에서
            # 통과해 흔들림으로 읽혔다). 엔트리포인트가 이 env 를 연 이유가 그것이다.
            "VINC_SIDECAR_PIDFILE": (self.out / "sidecar.pid").as_posix(),
            "VINC_EMBED_PIDFILE": (self.out / "embed.pid").as_posix(),
            "VINC_NODE_MIRROR_PIDFILE": (self.out / "mirror.pid").as_posix(),
        })
        env.pop("VINC_ROLE", None)
        driver = _DRIVER.format(entry=_ENTRY.as_posix(), out=self.out.as_posix())
        proc = subprocess.run(["sh", "-c", driver], cwd=str(_ROOT), env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, timeout=90)
        self.assertEqual(proc.returncode, 0, (proc.stdout or "")[-1500:])
        self.assertTrue(
            self._wait_for(self.out / "mirror-killed", 5.0),
            "도는 재구축이 TERM 을 못 받았다 — 닫으려는 사이드카를 붙들어 SIGKILL 을 "
            "부르고, 그 끝은 미완결 WAL 이다.\n" + (proc.stdout or "")[-1500:])

    def test_exec_로_넘기지_않는다__회귀_방지(self):
        """위 검사는 신호가 도는 환경을 요구한다. 이 검사는 환경과 무관하게
        **원인 자체**를 막는다 — `exec` 로 마지막 프로세스를 넘기면 trap 이 사라진다."""
        text = _ENTRY.read_text(encoding="utf-8")
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("#") or "serve-http" not in s:
                continue
            self.assertFalse(
                s.startswith("exec "),
                f"마지막 프로세스를 exec 로 넘기면 trap 이 사라진다: {s!r}")


if __name__ == "__main__":
    unittest.main()
