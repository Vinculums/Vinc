"""Check an installed integration distribution, independently of the source tree."""
from __future__ import annotations

import argparse
import ast
import importlib
import importlib.metadata
import shutil
import subprocess
import sys
from pathlib import Path


MODULES = {
    "vincs": ("vinc", ["Vinc", "AsyncVinc", "VincClient", "AsyncVincClient"]),
    "vinc-client": ("vinc_client", ["VincClient", "AsyncVincClient"]),
    "vinc-agent-framework": ("vinc_agent_framework", ["VincContextProvider", "create_vinc_tools"]),
    "vinc-langgraph": ("vinc_langgraph", ["get_vinc_context", "create_vinc_tools"]),
}
# 설치본의 CLI 런타임. SDK 를 import 했을 때 이 이름들이 올라오면 안 된다.
_RUNTIME = {"api", "store", "engines", "vinc_mcp", "vinc_rag"}


def _adapters() -> dict[str, str]:
    # 어댑터 엑스트라와 그 모듈. 이름 규칙(- 는 _)은 vincs 의 test_install.py 가 지킨다.
    extras = importlib.metadata.metadata("vincs").get_all("Provides-Extra") or []
    return {extra: f"vinc.integrations.{extra.replace('-', '_')}"
            for extra in extras if extra not in {"all", "test"}}


def _cli_version(package: Path) -> str:
    # `vinc --version` 이 말해야 하는 값: 휠에 실린 런타임의 제품 버전.
    tree = ast.parse((package / "_runtime" / "vinc_mcp" / "__init__.py").read_text(encoding="utf-8"))
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "__version__" for target in statement.targets):
            return ast.literal_eval(statement.value)
    raise SystemExit("the bundled runtime has no __version__")


def _check_vincs(module, extras: list[str]) -> None:
    eager = sorted(name for name in sys.modules
                   if name.startswith("vinc.integrations.") or name.split(".")[0] in _RUNTIME)
    if eager:
        raise SystemExit(f"import vinc loaded {eager}")
    adapters = _adapters()
    unknown = sorted(set(extras) - set(adapters) - {"all"})
    if unknown:
        raise SystemExit(f"vincs has no extra {unknown}; it has {sorted(adapters)} and all")
    enabled = set(adapters) if "all" in extras else set(extras)
    for extra, name in sorted(adapters.items()):
        try:
            adapter = importlib.import_module(name)
        except ModuleNotFoundError as exc:
            # 엑스트라 없이는 프레임워크가 없어서 실패해야 한다. vinc 자신의 파일이 빠진 것은 결함이다.
            if extra in enabled or exc.name == "vinc" or (exc.name or "").startswith("vinc."):
                raise SystemExit(f"{name} with extras {extras or ['none']}: {exc}") from exc
            print(f"{name}: not installed without vincs[{extra}] ({exc})")
            continue
        exports = list(getattr(adapter, "__all__", ()))
        missing = [export for export in exports if not hasattr(adapter, export)]
        if not exports or missing:
            raise SystemExit(f"{name} exports {exports}, missing {missing}")
        print(f"{name}: {len(exports)} exports")
    command = shutil.which("vinc", path=str(Path(sys.executable).parent))
    if command is None:
        raise SystemExit("no vinc command next to this interpreter")
    expected = f"vinc {_cli_version(Path(module.__file__).parent)}"
    for runner in ([command], [sys.executable, "-I", "-m", "vinc"]):
        usage = subprocess.run([*runner, "--help"], capture_output=True, text=True, check=True, timeout=120)
        if not usage.stdout.startswith("usage: vinc "):
            raise SystemExit(f"{runner[0]} --help does not name the command: {usage.stdout[:80]!r}")
        reported = subprocess.run([*runner, "--version"], capture_output=True, text=True,
                                  check=True, timeout=120).stdout.strip()
        if reported != expected:
            raise SystemExit(f"{runner[0]} --version reported {reported!r}, expected {expected!r}")
    print(f"vinc --version: {expected}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", choices=MODULES, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--extra", action="append", default=[],
                        help="vincs only: an extra this environment was installed with (repeatable)")
    args = parser.parse_args()
    if args.extra and args.package != "vincs":
        parser.error("--extra applies to vincs only")
    installed = importlib.metadata.version(args.package)
    if installed != args.version:
        raise SystemExit(f"installed {args.package} {installed}, expected {args.version}")
    module_name, exports = MODULES[args.package]
    module = importlib.import_module(module_name)
    if getattr(module, "__version__", None) != installed:
        raise SystemExit("module version does not match distribution metadata")
    for name in exports:
        if not callable(getattr(module, name, None)):
            raise SystemExit(f"missing callable export {module_name}.{name}")
    if args.package == "vincs":
        _check_vincs(module, args.extra)
    extras = f"[{','.join(args.extra)}]" if args.extra else ""
    print(f"{args.package}{extras} {installed}: installed import and public exports passed")


if __name__ == "__main__":
    main()
