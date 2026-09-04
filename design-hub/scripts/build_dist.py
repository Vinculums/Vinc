# -*- coding: utf-8 -*-
"""build_dist.py — SSoT 에서 dist/ 를 한 번에 다시 만든다(엔진 + 허브 후처리, 순서 고정).

여태 이 순서는 사람 손에 있었다(HUB-NOTES 에 흩어진 명령 넷). 모션 절(gen_motion)이
더해지면서 순서가 틀리면 조용히 빠지는 단계가 생겨 한 곳에 못 박는다:

  1. gen_tokens  DESIGN/design-tokens.json                      → DESIGN/tokens.css         (엔진)
  2. gen_tokens  base + tenants/desktop override                → tenants/desktop/design/tokens.css
  3. 복사        DESIGN/tokens.css                              → dist/site/site-tokens.css
  4. 복사        tenants/desktop/design/tokens.css              → dist/app/tokens.desktop.css
  5. scope       DESIGN/tokens.css (skin=web)                   → dist/app/tokens.web.css
  6. gen_motion  motion 그룹                                    → 3·4·5 산출물 각각에 삽입
  7. gen_theme_ts SSoT + viewScene                              → dist/app/theme.ts

그다음은 `scripts/sync_out.py --check|--apply` 로 소비 리포에 나른다.

usage: python -X utf8 scripts/build_dist.py [--check]
  --check 는 1·2 를 --check 로 돌려 SSoT 와 산출물이 맞는지만 본다(쓰지 않는다).
"""
from __future__ import annotations
import argparse, os, pathlib, shutil, subprocess, sys

HUB = pathlib.Path(__file__).resolve().parent.parent
PY = [sys.executable, "-X", "utf8"]


def plugin_root() -> pathlib.Path:
    env = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if env:
        return pathlib.Path(env)
    base = pathlib.Path.home() / ".claude" / "plugins" / "cache" / "lime-design" / "lime-design"
    return sorted(base.iterdir())[-1]


def run(*args: str) -> None:
    print("$", " ".join(str(a) for a in args))
    subprocess.run([*PY, *args], cwd=HUB, check=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    gen = str(plugin_root() / "scripts" / "gen_tokens.py")
    chk = ["--check"] if a.check else []
    run(gen, "--src", "DESIGN/design-tokens.json", "--out", "DESIGN/tokens.css",
        "--profile", "design.profile.yml", *chk)
    run(gen, "--base", "DESIGN/design-tokens.json", "--src", "tenants/desktop/design/override.tokens.json",
        "--out", "tenants/desktop/design/tokens.css", "--profile", "design.profile.yml", *chk)
    if a.check:
        print("build_dist --check: 엔진 산출물이 SSoT 와 맞는다")
        return 0
    (HUB / "dist/site").mkdir(parents=True, exist_ok=True)
    (HUB / "dist/app").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(HUB / "DESIGN/tokens.css", HUB / "dist/site/site-tokens.css")
    shutil.copyfile(HUB / "tenants/desktop/design/tokens.css", HUB / "dist/app/tokens.desktop.css")
    run("scripts/scope_tokens.py", "--src", "DESIGN/tokens.css", "--out", "dist/app/tokens.web.css", "--skin", "web")
    for f in ("dist/site/site-tokens.css", "dist/app/tokens.desktop.css", "dist/app/tokens.web.css"):
        run("scripts/gen_motion.py", "--tokens", "DESIGN/design-tokens.json", "--src", f)
    run("scripts/gen_theme_ts.py", "--tokens", "DESIGN/design-tokens.json",
        "--scene", "imports/figma-variables-2026-07-19.json", "--out", "dist/app/theme.ts")
    print("build_dist: dist/ 갱신 완료 — 다음은 scripts/sync_out.py --check|--apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
