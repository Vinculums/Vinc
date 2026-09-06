# -*- coding: utf-8 -*-
"""figma_app_copy.py — 앱 보드가 **지금의 코드**를 그리고 있는가.

2026-09-06 까지 이 게이트는 보드의 글자 하나하나를 `app/src` 리터럴과 대조했다. 보드를 사람이
그리던 때의 물음이다: 앱이 하지 않는 말이 화면에 서 있는가. 같은 날 앱 보드가 전부 **실행 중인
앱의 캡처**(scripts/capture_app_figma.mjs)로 바뀌면서 그 물음은 사라졌다 — 캡처 안의 모든 글자는
앱이 실제로 낸 것이다(코드 리터럴이거나, 코드가 조립한 문장이거나, 스토어의 데이터다). 남는
물음은 하나다: **캡처가 낡았는가.** 보드는 캡처 시점의 코드를 그리고, 그 뒤 `app/src` 가 바뀌면
보드는 조용히 거짓이 된다.

**재는 방법은 커밋 계보가 아니라 내용이다**(`sourceTree` = `git rev-parse <rev>:app/src`).
처음에는 `sourceRev` 뒤에 `app/src` 를 건드린 커밋이 있으면 FAIL 이었는데, 그 방식으로는
**어디서 캡처해도 통과할 수 없다**: 이 리포는 스쿼시 머지를 쓰므로 브랜치에서 캡처하면
매니페스트에 브랜치 커밋이 박히고, 머지가 만든 새 커밋은 그 커밋의 자손이 아니면서
`app/src` 를 건드린 것으로 잡혀 main 이 다시 빨개진다. 실제로 2026-09-06 에 이 게이트에
걸린 첫 app 변경(#232)이 그 막다른 길을 드러냈다.

트리 지문은 그 막다른 길이 없다. **스쿼시 머지는 커밋을 바꾸지만 트리는 그대로 옮긴다** —
실측: 브랜치 `6424b5a:app/src` 와 스쿼시 `7caa51c:app/src` 가 둘 다 `df6d688` 이다. 그래서
브랜치에서 캡처하면 그 PR 도, 머지된 main 도 함께 통과한다. 물음에도 더 맞다: 보드가 낡았나는
역사의 모양이 아니라 **지금 코드가 그때 코드와 같은가**이다.

커밋 목록은 판정이 아니라 **사람에게 보여 줄 설명**으로만 남는다. 그래서 얕은 체크아웃도
더 이상 판정을 막지 못한다(`git rev-parse HEAD:app/src` 는 얕아도 답한다) — 목록만 빠진다.

처방은 하나, 다시 캡처하는 것. 브랜치에서 해도 된다.

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


def source_tree(rev: str = "HEAD", paths=WATCHED, repo: pathlib.Path = REPO) -> str:
    """감시 경로의 **내용** 지문. 경로가 하나면 그 트리 object id 그대로다.

    얕은 체크아웃에서도 답한다 — 이 물음에는 역사가 필요 없다. 경로를 못 읽으면 예외다:
    지문을 못 구한 채 통과시키면 그것이야말로 아무것도 안 지키는 게이트다."""
    ids = []
    for p in paths:
        out = subprocess.run(["git", "rev-parse", f"{rev}:{p}"], cwd=repo,
                             capture_output=True, text=True, encoding="utf-8")
        if out.returncode != 0:
            raise RuntimeError(out.stderr.strip() or f"git rev-parse failed for {rev}:{p}")
        ids.append(out.stdout.strip())
    if len(ids) == 1:
        return ids[0]
    import hashlib
    return hashlib.sha1("\n".join(ids).encode("utf-8")).hexdigest()


def changes_since(rev: str, paths=WATCHED, repo: pathlib.Path = REPO) -> list[str]:
    """`rev` 뒤에 감시 경로를 건드린 커밋들(짧은 해시 + 제목).

    얕은 체크아웃에서는 판정하지 않는다 — 예외다. 잘린 경계의 커밋은 부모가 없어 트리 전체가 "변경"으로
    읽히고(2026-09-06 CI 에서 PR 병합 커밋이 그렇게 잡혀 거짓 FAIL), 반대로 경계 너머의 진짜 변경은
    보이지 않아 거짓 PASS 가 된다. 워크플로가 fetch-depth 0 을 주는 것이 처방이고, 여기서는 그 전제를
    확인만 한다. `rev` 를 모르는 저장소도 같은 이유로 예외다 — 조용히 통과하지 않는다."""
    shallow = subprocess.run(["git", "rev-parse", "--is-shallow-repository"], cwd=repo, capture_output=True, text=True, encoding="utf-8")
    if shallow.stdout.strip() == "true":
        raise RuntimeError("shallow checkout: history is cut, so freshness cannot be judged (checkout with fetch-depth: 0)")
    out = subprocess.run(["git", "rev-list", "--format=%h %s", "--no-commit-header", f"{rev}..HEAD", "--", *paths],
                         cwd=repo, capture_output=True, text=True, encoding="utf-8")
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or f"git rev-list failed for {rev}")
    return [l for l in out.stdout.splitlines() if l.strip()]


def check(manifest: dict, changes: list[str], tree: str | None = None) -> dict:
    """판정. 매니페스트에 트리 지문이 없으면 그것부터 실패다 — 무엇을 그렸는지 모르는
    보드는 신선도를 말할 수 없다. `changes` 는 판정에 안 쓰인다(사람에게 보여 줄 설명)."""
    want = manifest.get("sourceTree")
    if not want:
        return {"ok": False, "reason": "manifest has no sourceTree", "changes": []}
    return {"ok": tree == want, "rev": manifest.get("sourceRev") or "", "want": want,
            "tree": tree or "", "changes": changes,
            "states": len(manifest.get("states", []))}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="앱 보드(캡처)가 지금의 app/src 를 그리고 있는가")
    ap.parse_args(argv)
    if not APP_MANIFEST.exists():
        print(f"[ERROR] 앱 캡처 매니페스트가 없다: {APP_MANIFEST}", file=sys.stderr)
        return 3
    manifest = json.loads(APP_MANIFEST.read_text(encoding="utf-8"))
    rev = manifest.get("sourceRev")
    try:
        tree = source_tree()
    except RuntimeError as e:
        print(f"[ERROR] 신선도를 재지 못했다: {e}", file=sys.stderr)
        return 3
    # 커밋 목록은 설명일 뿐이라 못 구해도 판정은 계속한다(얕은 체크아웃·모르는 리비전).
    note = ""
    try:
        changes = changes_since(rev) if rev else []
    except RuntimeError as e:
        changes, note = [], f"    (바뀐 커밋 목록은 못 냈다: {e})"
    r = check(manifest, changes, tree)
    print("── 앱 보드 신선도 (캡처한 app/src ↔ 지금의 app/src)")
    if not r.get("want"):
        print("  [FAIL] 매니페스트에 sourceTree 가 없다 — capture_app.mjs 를 다시 돌려 지문을 남길 것")
        return 1
    if not r["ok"]:
        print(f"  [FAIL] 보드가 낡았다 — 캡처한 app/src({r['want'][:7]}) 와 지금({r['tree'][:7]}) 이 다르다.")
        print("         다시 캡처할 것. **브랜치에서 해도 된다** — 스쿼시 머지는 app/src 트리를")
        print("         그대로 옮기므로, 브랜치에서 통과하면 머지된 main 도 통과한다.")
        if r["changes"]:
            print(f"         캡처({r['rev'][:7]}) 뒤에 app/src 를 바꾼 커밋 {len(r['changes'])}건:")
            for c in r["changes"][:12]:
                print(f"           {c}")
        elif note:
            print(note)
        return 1
    print(f"  통과 · 상태 {r['states']}건이 그리는 app/src({r['want'][:7]}) 가 지금과 같다")
    return 0


if __name__ == "__main__":
    sys.exit(main())
