# -*- coding: utf-8 -*-
"""사이드카 워치독 — `packaging/docker-entrypoint.sh`.

2026-08-28 실측: 사이드카가 네이티브 힙 중단으로 죽은 뒤 워치독이 **34분 동안** 다시
띄우기만 했다. 스크립트 주석은 "5회 연속 실패하면 포기한다"고 적혀 있었지만 그 분기는
**도달 불가**였다 — 루프 머리에서 pid 가 살아 있으면 카운터를 0 으로 되돌리는데, 떴다가
죽는 프로세스는 그 순간 살아 있다. 로그에 재기동 14회가 전부 `1/5` 로 찍혔다.

그리고 포기해도 소용이 없었다. 작업본이 컨테이너 로컬이고 복원이 `restore_if_missing`
이라, 같은 컨테이너 안에서는 망가진 작업본을 몇 번을 다시 열어도 같은 결과다. 실제
복구는 새 레플리카뿐이었다.

정본: [[vinc.ops.incident-2026-08-28-hub-sidecar]] §3·§4.

그래서 여기서 재는 것은 성능도 타이밍도 아니라 **그 분기에 실제로 도달하는가**, 그리고
도달했을 때 **컨테이너가 내려가는가** 다. 기존 종료 검사와 같은 방식으로 진짜
스크립트를 돌리고 `python`·`curl` 만 스텁으로 가로챈다.

⚠ 구 코드에서 실제로 실패하는지 확인했다(구 버전 = 타임아웃/미도달 · 현 버전 = PASS).
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import unittest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_ENTRY = _ROOT / "packaging" / "docker-entrypoint.sh"

# 헬스는 **항상 ok** 라고 답한다. 이 검사의 고장 모드는 "헬스가 안 붙는 것"이 아니라
# "붙었다가 프로세스가 죽는 것"이고, 후자가 카운터를 못 올리던 바로 그 경우다.
_CURL = '#!/bin/sh\necho \'{"status": "ok"}\'\n'

# 사이드카는 뜨자마자 잠깐 살고 죽는다 — 헬스 창(30s)보다 짧게 살면 헬스조차 못 붙어
# 다른 갈래를 타므로, **헬스는 붙고 그 다음에 죽는** 모양으로 만든다. 그것이 실제
# 사고의 모양이고 구 카운터가 못 세던 모양이다.
_PY = """#!/bin/sh
OUT="{out}"
case "$*" in
  *"api.cli serve"*)
      n=$(cat "$OUT/starts" 2>/dev/null || echo 0)
      echo $((n + 1)) > "$OUT/starts"
      sleep 7
      exit 139 ;;
  *"mcp serve-http"*)
      echo started > "$OUT/mcp-started"
      trap 'exit 0' TERM
      while true; do sleep 0.2; done ;;
  *) exit 0 ;;
esac
"""

# 엔트리포인트를 띄우고 **가만히 둔다.** 아무도 신호를 보내지 않는다 — 스스로 포기하고
# 내려가야 통과다. 그것이 계약이다.
#
# ⚠ 실패했을 때의 정리가 이 드라이버의 절반이다. 처음에는 시간이 다 되면 `kill -KILL`
# 을 보냈는데, SIGKILL 은 트랩을 안 태우므로 **자식들이 고아로 남았다.** 그리고 검사가
# 임시 디렉터리를 지우면 PATH 의 스텁이 사라져 워치독의 다음 재기동이 **진짜 파이썬**
# 사이드카를 띄웠다. 그것들이 살아남아 다음 실행의 공유 pidfile·포트를 오염시켜, 뒤이은
# 검사가 "사이드카 정상"으로 오판하며 통과 실패를 반복했다(실측: 51개 누수).
# 그래서 TERM 을 먼저 보내 스크립트 자신의 `shutdown()` 이 자식을 정리하게 한다.
_DRIVER = """
sh "{entry}" > "{out}/log" 2>&1 &
ENTRY=$!
i=0
while [ "$i" -lt 600 ] && kill -0 "$ENTRY" 2>/dev/null; do i=$((i+1)); sleep 0.2; done
rc=0
if kill -0 "$ENTRY" 2>/dev/null; then
  echo "=== 스크립트가 스스로 내려오지 않았다 ==="
  rc=1
  kill -TERM "$ENTRY" 2>/dev/null          # 트랩을 태워 자식까지 정리시킨다
  j=0
  while [ "$j" -lt 100 ] && kill -0 "$ENTRY" 2>/dev/null; do j=$((j+1)); sleep 0.2; done
  kill -KILL "$ENTRY" 2>/dev/null          # 그래도 남으면 마지막 수단
fi
cat "{out}/log"
exit "$rc"
"""


@unittest.skipUnless(shutil.which("sh"), "POSIX sh 없음")
class WatchdogGivesUpTests(unittest.TestCase):
    """계약: 재기동이 상한만큼 실패하면 **컨테이너를 내린다.**"""

    def setUp(self):
        import tempfile
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="vinc-wd-"))
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

    def _run(self) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env.update({
            "PATH": str(self.bin) + os.pathsep + env.get("PATH", ""),
            "VINC_SIDECAR_TOKEN": "t",
            "VINC_DATA_DIR": (self.out / "db").as_posix(),
            "VINC_EMBED": "off",
            "VINC_MCP_STATE_DIR": (self.out / "state").as_posix(),
            "VINC_MCP_STATE_SNAPSHOT": (self.out / "snap" / "oauth.db").as_posix(),
            # ⚠ pidfile 을 **이 실행 전용**으로 준다. 기본값은 `/tmp/vinc-sidecar.pid`
            # 고정이고, 컨테이너 안에서는 그것으로 충분하지만 한 기계에서 여러 번 돌면
            # 서로 덮어쓴다 — 워치독이 남의 살아 있는 pid 를 읽고 사이드카가 멀쩡하다고
            # 판단해 이 검사가 **조용히 무의미해진다**(실측으로 그렇게 됐다).
            "VINC_SIDECAR_PIDFILE": (self.out / "sidecar.pid").as_posix(),
            "VINC_EMBED_PIDFILE": (self.out / "embed.pid").as_posix(),
            # 상한을 낮춰 검사를 짧게 만든다. 상한 자체가 env 로 열려 있다는 것도
            # 이 검사가 함께 고정하는 계약이다(운영 중 조절 여지).
            "VINC_SIDECAR_MAX_RESTARTS": "2",
            # 안정 판정 창을 크게 둬서 **회복으로 오인되지 않게** 한다. 이 값이
            # 작으면 떴다 죽는 것이 회복으로 세어져 구 결함이 되살아난다.
            "VINC_SIDECAR_STABLE_S": "9999",
        })
        env.pop("VINC_ROLE", None)
        driver = _DRIVER.format(entry=_ENTRY.as_posix(), out=self.out.as_posix())
        # ⚠ 인코딩을 명시한다. `text=True` 만 주면 로케일 코덱으로 디코드하는데,
        # 이 스크립트의 로그는 한국어라 cp949 Windows 에서 `UnicodeDecodeError` 로
        # 리더 스레드가 죽고 **판정이 아니라 환경이 실패한다.**
        return subprocess.run(["sh", "-c", driver], cwd=str(_ROOT), env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              encoding="utf-8", errors="replace", timeout=180)

    def test_상한을_넘기면_컨테이너가_내려간다(self):
        proc = self._run()
        log = proc.stdout or ""
        self.assertEqual(
            proc.returncode, 0,
            "워치독이 포기하지 않고 무한 재기동했다 — 2026-08-28 에 34분을 그렇게 "
            "보냈다. 이 안에서 다시 여는 것으로는 못 고치는 상태라 컨테이너가 "
            "내려가야 새 레플리카가 스냅샷에서 복원한다.\n" + log[-2500:])
        self.assertIn("컨테이너를 내린다", log,
                      "포기 분기를 안 탔다(다른 이유로 종료했을 수 있다).\n" + log[-2500:])

    def test_카운터가_실제로_올라간다__1_에서_멈추지_않는다(self):
        """구 결함의 지문. 로그에 `1/N` 만 있고 `2/N` 이 없으면 카운터가 매 회
        0 으로 되돌아가고 있다는 뜻이고, 그러면 포기 분기는 영원히 안 온다."""
        proc = self._run()
        log = proc.stdout or ""
        self.assertIn("재기동 시도 1/2", log, log[-2500:])
        self.assertIn("재기동 시도 2/2", log,
                      "카운터가 1 에서 멈췄다 — 살아 있음을 회복으로 세고 있다.\n"
                      + log[-2500:])


if __name__ == "__main__":
    unittest.main()
