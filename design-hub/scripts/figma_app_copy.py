# -*- coding: utf-8 -*-
"""figma_app_copy.py — 앱 보드가 **지금의 코드**를 그리고 있는가.

2026-09-06 까지 이 게이트는 보드의 글자 하나하나를 `app/src` 리터럴과 대조했다. 보드를 사람이
그리던 때의 물음이다: 앱이 하지 않는 말이 화면에 서 있는가. 같은 날 앱 보드가 전부 **실행 중인
앱의 캡처**(scripts/capture_app_figma.mjs)로 바뀌면서 그 물음은 사라졌다 — 캡처 안의 모든 글자는
앱이 실제로 낸 것이다(코드 리터럴이거나, 코드가 조립한 문장이거나, 스토어의 데이터다). 남는
물음은 하나다: **캡처가 낡았는가.** 보드는 캡처 시점의 코드를 그리고, 그 뒤 `app/src` 가 바뀌면
보드는 조용히 거짓이 된다. 그래서 이 게이트는 매니페스트가 적어 둔 리비전(`sourceRev`) 뒤에
`app/src` 를 건드린 커밋이 있으면 FAIL 이다. 처방은 하나, 다시 캡처하는 것.

    python -X utf8 scripts/figma_app_copy.py          # FAIL 이면 exit 1 과 커밋 목록

근거: [[decision:site-boards-are-captures-of-the-running-code]].
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

HUB = pathlib.Path(__file__).resolve().parent.parent
REPO = HUB.parent
APP_MANIFEST = HUB / "reports" / "captures" / "app" / "manifest.json"
#: 캡처가 그린 것. 이 밖의 파일이 바뀌어도 보드는 안 낡는다.
WATCHED = ("app/src",)


def changes_since(rev: str, paths=WATCHED, repo: pathlib.Path = REPO) -> list[str]:
    """`rev` 뒤에 감시 경로를 건드린 커밋들(짧은 해시 + 제목). CI 는 fetch-depth 0 이라 역사가 있다.
    `rev` 를 모르는 얕은 체크아웃이면 git 이 실패하고, 그 사실을 예외로 올린다 — 조용히 통과하지 않는다."""
    out = subprocess.run(["git", "rev-list", "--format=%h %s", "--no-commit-header", f"{rev}..HEAD", "--", *paths],
                         cwd=repo, capture_output=True, text=True, encoding="utf-8")
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or f"git rev-list failed for {rev}")
    return [l for l in out.stdout.splitlines() if l.strip()]


def check(manifest: dict, changes: list[str]) -> dict:
    """판정. 매니페스트에 리비전이 없으면 그것부터 실패다 — 무엇을 그렸는지 모르는 보드는 신선도를 말할 수 없다."""
    rev = manifest.get("sourceRev")
    if not rev:
        return {"ok": False, "reason": "manifest has no sourceRev", "changes": []}
    return {"ok": not changes, "rev": rev, "changes": changes, "states": len(manifest.get("states", []))}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="앱 보드(캡처)가 지금의 app/src 를 그리고 있는가")
    ap.parse_args(argv)
    if not APP_MANIFEST.exists():
        print(f"[ERROR] 앱 캡처 매니페스트가 없다: {APP_MANIFEST}", file=sys.stderr)
        return 3
    manifest = json.loads(APP_MANIFEST.read_text(encoding="utf-8"))
    rev = manifest.get("sourceRev")
    try:
        changes = changes_since(rev) if rev else []
    except RuntimeError as e:
        print(f"[ERROR] 신선도를 재지 못했다: {e}", file=sys.stderr)
        return 3
    r = check(manifest, changes)
    print("── 앱 보드 신선도 (캡처 리비전 ↔ app/src)")
    if not r["ok"] and not r.get("rev"):
        print("  [FAIL] 매니페스트에 sourceRev 가 없다 — capture_app.mjs 를 다시 돌려 리비전을 남길 것")
        return 1
    if r["changes"]:
        print(f"  [FAIL] 캡처({r['rev'][:7]}) 뒤에 app/src 를 바꾼 커밋 {len(r['changes'])}건 — 보드가 낡았다. 다시 캡처할 것:")
        for c in r["changes"][:12]:
            print(f"    {c}")
        return 1
    print(f"  통과 · 상태 {r['states']}건이 {r['rev'][:7]} 의 app/src 를 그린다. 그 뒤 app/src 변경 없음")
    return 0


if __name__ == "__main__":
    sys.exit(main())
