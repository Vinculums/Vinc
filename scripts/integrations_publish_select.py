"""Select one integration distribution from a release tag or a TestPyPI dispatch."""
from __future__ import annotations

import argparse
import tomllib
from pathlib import Path


PACKAGE_DIRS = {
    "vincs": "integrations/vincs/python",
    "vinc-client": "integrations/client/python",
    "vinc-agent-framework": "integrations/agent-framework/python",
    "vinc-langgraph": "integrations/langgraph/python",
}


def select_package(event: str, ref: str, package: str, root: Path) -> tuple[str, str, str]:
    if event == "push":
        if not ref.startswith("refs/tags/"):
            raise ValueError("package publishing requires a tag push")
        tag = ref.removeprefix("refs/tags/")
        matches = [(name, tag.removeprefix(f"integrations-{name}-v"))
                   for name in PACKAGE_DIRS if tag.startswith(f"integrations-{name}-v")]
        if len(matches) != 1:
            raise ValueError("tag must name exactly one integration package")
        name, tag_version = matches[0]
        if package:
            raise ValueError("a tag push cannot override its package")
    elif event == "workflow_dispatch":
        if ref != "refs/heads/main":
            raise ValueError("TestPyPI dispatch must use main")
        if package not in PACKAGE_DIRS:
            raise ValueError("choose one known integration package")
        name, tag_version = package, None
    else:
        raise ValueError("unsupported publishing event")

    path = PACKAGE_DIRS[name]
    project_file = root / path / "pyproject.toml"
    if not project_file.is_file():
        raise ValueError(f"package source is absent: {path}")
    metadata = tomllib.loads(project_file.read_text(encoding="utf-8"))
    if metadata.get("project", {}).get("name") != name:
        raise ValueError(f"project name does not match {name}")
    version = metadata["project"]["version"]
    if tag_version is not None and tag_version != version:
        raise ValueError(f"tag version {tag_version!r} does not match {name} {version}")
    return name, path, version


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--package", default="")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--github-output", type=Path, required=True)
    args = parser.parse_args()
    name, path, version = select_package(args.event, args.ref, args.package, args.root)
    with args.github_output.open("a", encoding="utf-8") as output:
        output.write(f"package={name}\npath={path}\nversion={version}\n")


if __name__ == "__main__":
    main()
