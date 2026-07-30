# -*- coding: utf-8 -*-
"""공식 스타터팩 시딩용 JSON 팩 + 매니페스트 생성.

**왜 변환이 필요한가.** D55 이후 `.vincpack` 은 zip 컨테이너다(`api/packfile.py`).
그런데 커뮤니티 갤러리의 저장 경로는 **JSON 전용**이다 — `community_packs.pack_json`
이 `MEDIUMTEXT` 이고 다운로드가 `Content-Type: application/json` 을 낸다. 이건 버그가
아니라 packfile 의 명시적 계약이다: "**읽기는 두 형식 모두** 받고 **쓰기만** zip".
갤러리에는 JSON 으로 넣는 것이 정상이고, 앱은 하위호환으로 그대로 읽는다.

`documents`(원문)는 그대로 실린다 — 자기완결 공유(D45)가 갤러리에서도 유지된다.

**이 스크립트가 존재하는 이유.** 시딩은 마이그레이션이 아니라 **운영자 수동 단계**라,
DB 를 새로 만들면 공식팩만 조용히 사라진다. Azure 이관에서 실제로 그렇게 됐고
(2026-07-30, `community_packs` 0행) 갤러리가 빈 채 서빙되고 있었다. 그때 손으로 만든
변환 절차를 커밋해 다음 사람이 다시 발명하지 않게 한다.

사용:
    python tools/make_seed_manifest.py [--out DIR]
    # 그 다음 (Vinc-homepage/account 에서, DB env 주입 후)
    npx tsx scripts/seed-official-packs.ts <DIR>/official-packs.json

⚠ 크기 상한 2MB(`routes/community.ts` MAX_PACK_BYTES)를 넘으면 시딩이 SKIP 된다.
   이 스크립트가 초과분을 먼저 알려준다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
PACKS = TOOLS.parent                       # starter-packs/
sys.path.insert(0, str(TOOLS.parents[1]))  # 리포 루트 — api.packfile 을 쓴다
from api import packfile                   # noqa: E402

MAX_PACK_BYTES = 2 * 1024 * 1024           # routes/community.ts 와 같은 값


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(PACKS / "seed-json"),
                    help="JSON 팩과 매니페스트를 낼 디렉터리(기본: starter-packs/seed-json)")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    src = json.loads((PACKS / "official-packs.json").read_text(encoding="utf-8"))
    manifest, over = [], []
    for e in src:
        zpath = PACKS / e["file"]
        if not zpath.exists():
            print(f"  MISSING {e['file']}", file=sys.stderr)
            return 1
        pack = packfile.from_bytes(zpath.read_bytes())      # zip → dict (documents 포함)
        name = e["file"].replace(".vincpack", ".json.vincpack")
        text = json.dumps(pack, ensure_ascii=False, indent=2) + "\n"
        size = len(text.encode("utf-8"))
        (out / name).write_text(text, encoding="utf-8")
        entry = dict(e); entry["file"] = name
        manifest.append(entry)
        flag = "  ⚠ 2MB 초과 — 시딩이 SKIP 한다" if size > MAX_PACK_BYTES else ""
        if size > MAX_PACK_BYTES:
            over.append(name)
        print(f"  {e['file']:34} -> {name}  {size/1024/1024:.2f}MB  "
              f"docs={len(pack.get('documents') or [])}{flag}")

    (out / "official-packs.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n매니페스트: {out / 'official-packs.json'} ({len(manifest)}개)")
    if over:
        print(f"⚠ 상한 초과 {len(over)}건 — 시딩 전에 해결할 것: {', '.join(over)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
