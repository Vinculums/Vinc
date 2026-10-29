# -*- coding: utf-8 -*-
"""third_party_notices.py — 앱과 설치본에 실리는 서드파티 고지 파일을 만든다.

설치본에는 세 생태계의 코드가 실린다: 앱 화면(npm), 셸(Rust 크레이트), 동결 사이드카
(파이썬 패키지와 CPython 런타임, PyInstaller 부트로더). 이 파일이 셋을 모아
`app/public/third-party/THIRD_PARTY_NOTICES.md` 에 쓴다. Vite 가 `public/` 을 그대로
복사하므로 데스크톱 번들과 웹 빌드 양쪽에 같은 고지가 실린다.

    python -X utf8 scripts/third_party_notices.py            # 쓴다
    python -X utf8 scripts/third_party_notices.py --check    # 커밋된 사본과 같은지 본다

무엇을 세는가:
- npm: `app/package-lock.json` 에서 dev 가 아닌 패키지. 라이선스 본문은 `app/node_modules` 에서 읽는다.
- Rust: `cargo metadata` 의 해석 그래프에서 루트로부터 일반(normal) 의존으로 닿는 크레이트.
  빌드 전용 의존은 실리지 않으므로 뺀다. 대상 플랫폼은 가리지 않는다(세 플랫폼의 합집합).
- 파이썬: `--python-top` 으로 받은 최상위 패키지(동결 사이드카에 넣는 목록)와 그 의존의 닫힘.
  지금 인터프리터에 깔린 판을 읽으므로 릴리스 빌드 안에서 돌려야 정확하다.

같은 본문은 한 번만 싣고, 그 본문을 쓰는 패키지를 함께 적는다. 경로·시각은 싣지 않는다.
근거: [[vinc.plan.license-structure-2026-10-29]]
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as md
import json
import pathlib
import re
import subprocess
import sys
import sysconfig

ROOT = pathlib.Path(__file__).resolve().parent.parent
APP = ROOT / "app"
TAURI = APP / "src-tauri"
OUT = APP / "public" / "third-party" / "THIRD_PARTY_NOTICES.md"
FONT_LICENSE = APP / "public" / "third-party" / "Pretendard-OFL.txt"
PYTHON_TOP = ("ladybug", "pyyaml", "pypdf")
LICENSE_FILE = re.compile(r"^(licen[cs]e|copying|notice|copyright)([-_.].*)?$", re.I)


class NoticeError(SystemExit):
    pass


def _texts(directory: pathlib.Path) -> list[str]:
    if not directory.is_dir():
        return []
    out = []
    for p in sorted(directory.iterdir(), key=lambda x: x.name.lower()):
        if p.is_file() and LICENSE_FILE.match(p.name):
            out.append(p.read_text(encoding="utf-8", errors="replace").strip())
    return out


def npm_packages() -> list[dict]:
    lock = json.loads((APP / "package-lock.json").read_text(encoding="utf-8"))
    rows = []
    for key, meta in lock.get("packages", {}).items():
        if not key or meta.get("dev") or meta.get("devOptional"):
            continue
        name = key.split("node_modules/")[-1]
        rows.append({"eco": "npm", "name": name, "version": meta.get("version", ""),
                     "license": meta.get("license") or "see text",
                     "texts": _texts(APP / key)})
    return rows


def cargo_packages() -> list[dict]:
    try:
        raw = subprocess.run(["cargo", "metadata", "--format-version", "1", "--locked"],
                             cwd=TAURI, check=True, capture_output=True, text=True).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise NoticeError(f"[third_party_notices] cargo metadata 실패: {exc}") from exc
    meta = json.loads(raw)
    pkgs = {p["id"]: p for p in meta["packages"]}
    nodes = {n["id"]: n for n in meta["resolve"]["nodes"]}
    root = meta["resolve"]["root"]
    seen, todo = set(), [root]
    while todo:
        cur = todo.pop()
        for dep in nodes[cur]["deps"]:
            if any(k.get("kind") is None for k in dep["dep_kinds"]) and dep["pkg"] not in seen:
                seen.add(dep["pkg"])
                todo.append(dep["pkg"])
    rows = []
    for pid in seen:
        p = pkgs[pid]
        rows.append({"eco": "cargo", "name": p["name"], "version": p["version"],
                     "license": p.get("license") or "see text",
                     "texts": _texts(pathlib.Path(p["manifest_path"]).parent)})
    return rows


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _dist_texts(dist: md.Distribution) -> list[str]:
    out = []
    for f in sorted(dist.files or [], key=str):
        if LICENSE_FILE.match(pathlib.PurePath(str(f)).name) and ".dist-info" in str(f):
            try:
                out.append(pathlib.Path(dist.locate_file(f)).read_text(encoding="utf-8", errors="replace").strip())
            except OSError:
                pass
    return out


def _dist_license(dist: md.Distribution) -> str:
    m = dist.metadata
    expr = m.get("License-Expression")
    if expr:
        return expr
    cls = [c.split("::")[-1].strip() for c in (m.get_all("Classifier") or []) if c.startswith("License ::")]
    if cls:
        return " / ".join(cls)
    lic = (m.get("License") or "").strip()
    return lic.splitlines()[0][:80] if lic else "see text"


def python_packages(top: tuple[str, ...]) -> list[dict]:
    by_name = {_norm(d.metadata["Name"]): d for d in md.distributions() if d.metadata["Name"]}
    try:
        from packaging.requirements import Requirement
    except ImportError:
        Requirement = None
    seen, todo, rows = set(), [_norm(t) for t in top], []
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        dist = by_name.get(name)
        if dist is None:
            raise NoticeError(f"[third_party_notices] 파이썬 패키지 {name} 가 이 인터프리터에 없다 "
                              f"(동결 사이드카와 같은 환경에서 돌릴 것)")
        seen.add(name)
        rows.append({"eco": "python", "name": dist.metadata["Name"], "version": dist.version,
                     "license": _dist_license(dist), "texts": _dist_texts(dist)})
        for req in dist.requires or []:
            if Requirement is None:
                head = re.split(r"[ ;<>=!~\[(]", req, maxsplit=1)[0]
                if "extra ==" not in req:
                    todo.append(_norm(head))
                continue
            r = Requirement(req)
            if r.marker is None or r.marker.evaluate({"extra": ""}):
                todo.append(_norm(r.name))
    return rows


def runtime_extras() -> list[dict]:
    """패키지 목록에 안 잡히지만 설치본에 실리는 것."""
    rows = []
    # 리눅스·macOS 는 표준 라이브러리 폴더에, 윈도우는 설치 뿌리에 둔다.
    cpython = next((p for p in (pathlib.Path(sysconfig.get_paths()["stdlib"]) / "LICENSE.txt",
                                pathlib.Path(sys.base_prefix) / "LICENSE.txt") if p.is_file()),
                   pathlib.Path(sys.base_prefix) / "LICENSE.txt")
    rows.append({"eco": "runtime", "name": "CPython", "version": ".".join(map(str, sys.version_info[:3])),
                 "license": "PSF-2.0",
                 "texts": [cpython.read_text(encoding="utf-8", errors="replace").strip()] if cpython.is_file() else []})
    try:
        pyi = md.distribution("pyinstaller")
        rows.append({"eco": "runtime", "name": "PyInstaller bootloader", "version": pyi.version,
                     "license": "GPL-2.0-or-later WITH Bootloader-exception", "texts": _dist_texts(pyi)})
    except md.PackageNotFoundError:
        pass
    # 확장 셋(algo · fts · vector)은 packaging/lbug_extensions 에서 사이드카에 함께 실린다. 같은 프로젝트의 산출물이다.
    try:
        lbug = md.distribution("ladybug")
        rows.append({"eco": "runtime", "name": "LadybugDB extensions (algo, fts, vector)", "version": lbug.version,
                     "license": _dist_license(lbug), "texts": _dist_texts(lbug)})
    except md.PackageNotFoundError:
        pass
    rows.append({"eco": "font", "name": "Pretendard", "version": "",
                 "license": "OFL-1.1", "texts": [FONT_LICENSE.read_text(encoding="utf-8").strip()]})
    return rows


SECTIONS = [("npm", "JavaScript packages in the app"), ("cargo", "Rust crates in the desktop shell"),
            ("python", "Python packages in the desktop sidecar"), ("runtime", "Runtimes"), ("font", "Fonts")]


def render(rows: list[dict]) -> str:
    texts: dict[str, dict] = {}
    for r in rows:
        r["refs"] = []
        for t in r["texts"]:
            h = hashlib.sha256(t.encode()).hexdigest()[:12]
            texts.setdefault(h, {"text": t, "users": []})["users"].append(f"{r['name']} {r['version']}".strip())
            r["refs"].append(h)
    L = ["# Third-party notices", "",
         "Vinc includes the third-party software listed below. Each component is used under its own",
         "license, reproduced in full at the end of this file. Vinc's own license is in `LICENSE` and",
         "`LICENSING.md`. This file is generated by `scripts/third_party_notices.py` when a release is built.",
         ""]
    for eco, title in SECTIONS:
        part = sorted((r for r in rows if r["eco"] == eco), key=lambda r: (r["name"].lower(), r["version"]))
        if not part:
            continue
        L += [f"## {title}", "", "| Component | Version | License | Text |", "|---|---|---|---|"]
        for r in part:
            ref = ", ".join(f"[{h}](#text-{h})" for h in r["refs"]) or "not included in the package"
            L.append(f"| {r['name']} | {r['version']} | {r['license'].replace('|', '/')} | {ref} |")
        L.append("")
    L += ["## License texts", ""]
    for h in sorted(texts):
        users = sorted(set(texts[h]["users"]), key=str.lower)
        L += [f'<a id="text-{h}"></a>', f"### {h}", "", "Used by: " + ", ".join(users), "", "```",
              texts[h]["text"].replace("```", "'''"), "```", ""]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--python-top", nargs="*", default=list(PYTHON_TOP))
    a = ap.parse_args(argv)
    rows = npm_packages() + cargo_packages() + python_packages(tuple(a.python_top)) + runtime_extras()
    text = render(rows)
    if a.check:
        if not OUT.is_file() or OUT.read_text(encoding="utf-8") != text:
            print(f"[third_party_notices] {OUT.relative_to(ROOT)} 가 지금 환경에서 만든 것과 다르다", file=sys.stderr)
            return 1
        print("[third_party_notices] 같다")
        return 0
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"[third_party_notices] {OUT.relative_to(ROOT)}: 구성요소 {len(rows)}개 · 본문 "
          f"{text.count('### ')}개 · {len(text) // 1024} KiB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
