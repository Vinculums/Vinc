"""Run the existing product CLI without exposing its internal module names."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args[:2] == ["eval", "run"]:
        from .kit.cli import eval_main
        return eval_main(args[1:])
    if args[:1] == ["agent"]:
        # 에이전트 실행기는 사용자의 인터프리터에서 돈다: 프레임워크와 모델 패키지는 거기에 있다.
        from .kit.cli import main as agent_main
        return agent_main(args[1:])
    # CLI 내부의 api/store 이름을 SDK 사용자의 인터프리터에 등록하지 않는다.
    # 자식은 부모의 stdin/stdout/stderr와 종료 코드를 그대로 사용한다.
    # argv[0] 을 "vinc" 로 두어야 도움말과 사용법 오류가 `-c` 가 아니라 명령 이름을 말한다.
    runtime = Path(__file__).resolve().parent / "_runtime"
    code = (
        "import sys; sys.path.insert(0, sys.argv.pop(1)); sys.argv[0] = 'vinc'; "
        "from api.cli import main; raise SystemExit(main())"
    )
    command = [sys.executable, "-I", "-c", code, str(runtime),
               *(sys.argv[1:] if argv is None else argv)]
    try:
        return subprocess.call(command)
    except KeyboardInterrupt:
        return 130
