"""Check an installed integration distribution, independently of the source tree."""
from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import subprocess
import sys


MODULES = {
    "vincs": ("vinc", ["Vinc", "AsyncVinc", "VincClient", "AsyncVincClient"]),
    "vinc-client": ("vinc_client", ["VincClient", "AsyncVincClient"]),
    "vinc-agent-framework": ("vinc_agent_framework", ["VincContextProvider", "create_vinc_tools"]),
    "vinc-langgraph": ("vinc_langgraph", ["get_vinc_context", "create_vinc_tools"]),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", choices=MODULES, required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
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
        subprocess.run([sys.executable, "-I", "-m", "vinc", "--help"], check=True)
        subprocess.run([sys.executable, "-I", "-m", "vinc", "--version"], check=True)
    print(f"{args.package} {installed}: installed import and public exports passed")


if __name__ == "__main__":
    main()
