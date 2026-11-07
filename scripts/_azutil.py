"""운영 스크립트의 Azure CLI 호출 한 벌.

`az` 는 `shutil.which` 로 찾는다. 윈도우의 `az` 는 `az.cmd` 라 이름만 넘기면
`CreateProcess` 가 못 찾는다. 인자는 리스트로 넘기므로 셸을 거치지 않고, 그래서
Git Bash 의 MSYS 경로 변환([[vinc.manual.platform]] §2)도 끼어들지 않는다.

실패는 `AzError` 다. 메시지에는 인자와 Azure 의 응답을 넣지 않는다: 시크릿을 실은
argv 나 ARM 템플릿·env 값이 로그에 복제된다. stderr 는 `.stderr` 로만 들고 다닌다.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Mapping, Sequence


class AzError(RuntimeError):
    """`az` 가 없거나(returncode None) 0 이 아닌 코드로 끝났다."""

    def __init__(self, message: str, *, returncode: int | None = None, stderr: str = ""):
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr


def az(args: Sequence[str], *, timeout: float | None = 600, output: str | None = "json",
       env: Mapping[str, str] | None = None):
    """`output="json"` 이면 `-o json` 을 붙이고 해석한 값을, None 이면 stdout 문자열을 준다."""
    executable = shutil.which("az")
    if not executable:
        raise AzError("az CLI is unavailable")
    tail = ["-o", output] if output else []
    result = subprocess.run([executable, *args, *tail], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=timeout, env=env)
    if result.returncode:
        raise AzError(f"az {' '.join(args[:2])} failed (exit {result.returncode})",
                      returncode=result.returncode, stderr=result.stderr)
    if output == "json":
        return json.loads(result.stdout or "null")
    return result.stdout
