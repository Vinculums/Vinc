"""Assemble the unified distribution from the canonical repository packages."""
from __future__ import annotations

import re
import tempfile
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        root = Path(self.root)
        # sdist 에는 이미 조립된 소스가 있다. 저장소 밖에서 다시 빌드할 수 있어야 한다.
        if (root / "src/vinc/sdk/client.py").is_file():
            return
        repo = root.parents[2]
        sources = {
            "sdk": repo / "integrations/client/python/src/vinc_client",
            "integrations/langgraph": repo / "integrations/langgraph/python/src/vinc_langgraph",
            "integrations/agent_framework": repo / "integrations/agent-framework/python/src/vinc_agent_framework",
            **{f"_runtime/{name}": repo / name
               for name in ("api", "store", "engines", "vinc_mcp", "vinc_rag")},
        }
        self._stage = tempfile.TemporaryDirectory(prefix="vincs-build-")
        stage = Path(self._stage.name)
        prefix = "src/vinc" if self.target_name == "sdist" else "vinc"
        for destination, source in sources.items():
            if not source.is_dir():
                raise RuntimeError(f"canonical package source missing: {source}")
            for path in sorted(source.rglob("*")):
                if not path.is_file() or "__pycache__" in path.parts:
                    continue
                if path.suffix not in {".py", ".json"} and path.name != "py.typed":
                    continue
                relative = Path(destination) / path.relative_to(source)
                target = stage / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                if path.suffix == ".py" and destination.startswith("integrations/"):
                    # 별도 배포판의 파일을 덮어쓰지 않고 같은 SDK 소스에 연결한다.
                    text = path.read_text(encoding="utf-8")
                    text = re.sub(r"(?m)^(from\s+)vinc_client(?=[.\s])", r"\1vinc.sdk", text)
                    target.write_text(text, encoding="utf-8")
                else:
                    target.write_bytes(path.read_bytes())
                build_data["force_include"][str(target)] = f"{prefix}/{relative.as_posix()}"

    def finalize(self, version, build_data, artifact_path):
        if hasattr(self, "_stage"):
            self._stage.cleanup()
