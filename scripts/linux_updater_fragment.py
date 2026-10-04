"""Linux 설치 형식마다 실제 서명된 같은 형식의 업데이터 자산을 연결한다."""
import json
from pathlib import Path
import sys
from urllib.parse import quote


def complete_linux_fragment(directory: Path, tag: str, repository: str) -> dict:
    fragment = directory / "latest-linux-x86_64.json"
    platforms = json.loads(fragment.read_text(encoding="utf-8"))
    generic = platforms["linux-x86_64"]
    if not generic["url"].endswith(".AppImage") or not generic["signature"].strip():
        raise ValueError("Linux fallback must be a signed AppImage")
    platforms["linux-x86_64-appimage"] = dict(generic)
    for extension in ("deb", "rpm"):
        # 별칭도 목록에 있지만 서명 파일은 버전 이름에만 있다.
        payloads = [p for p in directory.glob(f"*.{extension}") if Path(str(p) + ".sig").is_file()]
        if len(payloads) != 1:
            raise ValueError(f"expected exactly one signed {extension} payload")
        payload = payloads[0]
        signature = Path(str(payload) + ".sig").read_text(encoding="utf-8").strip()
        if not signature:
            raise ValueError(f"empty {extension} updater signature")
        platforms[f"linux-x86_64-{extension}"] = {
            "signature": signature,
            "url": f"https://github.com/{repository}/releases/download/{quote(tag, safe='')}/{quote(payload.name)}",
        }
    fragment.write_text(json.dumps(platforms, indent=2) + "\n", encoding="utf-8", newline="\n")
    return platforms


if __name__ == "__main__":
    complete_linux_fragment(Path(sys.argv[1]), sys.argv[2], sys.argv[3])
