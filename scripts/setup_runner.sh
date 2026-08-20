#!/usr/bin/env bash
# 셀프호스티드 러너에 필요한 것을 깔고 확인한다. 목록 정본은 `scripts/runner-tools.txt`.
#
#   bash scripts/setup_runner.sh            # 확인만(아무것도 안 깐다)
#   bash scripts/setup_runner.sh --install   # 없는 것 설치(apt 계열만)
#
# ⚠ **러너가 쓰는 셸에서 돌릴 것.** Windows 러너의 도구는 Git Bash 의 `/usr/bin` 에
#   있고 Windows PATH 에는 없다 — pwsh 에서 확인하면 있는 것을 없다고 답한다.
#   Windows 는 자동 설치를 하지 않는다: 패키지 관리자가 있다는 보장이 없고, 검증 못 한
#   설치가 러너를 망가뜨리는 것이 "도구가 없다"보다 나쁘다. 어디에 두면 되는지만 말한다.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIST="$ROOT/scripts/runner-tools.txt"
[ -f "$LIST" ] || { echo "목록이 없다: $LIST" >&2; exit 2; }

case "$(uname -s)" in
  Linux*)  PLATFORM=linux ;;
  *)       PLATFORM=windows ;;    # MINGW·MSYS — Git Bash
esac
INSTALL=0
[ "${1:-}" = "--install" ] && INSTALL=1

# 이 플랫폼에 해당하는 이름만 뽑는다.
want=()
while IFS= read -r line; do
  line="${line%%#*}"
  line="$(echo "$line" | xargs)"
  [ -z "$line" ] && continue
  name="${line%% *}"
  scope="${line#"$name"}"; scope="$(echo "$scope" | xargs)"
  if [ -n "$scope" ]; then
    [ "$scope" = "@$PLATFORM" ] || continue
  fi
  want+=("$name")
done < "$LIST"

missing=()
for c in "${want[@]}"; do
  command -v "$c" >/dev/null 2>&1 || missing+=("$c")
done

if [ ${#missing[@]} -eq 0 ]; then
  echo "[setup_runner] OK ($PLATFORM) — ${want[*]}"
  exit 0
fi

echo "[setup_runner] 없는 것: ${missing[*]}"
if [ "$INSTALL" != "1" ]; then
  echo "  설치하려면: bash scripts/setup_runner.sh --install"
  exit 1
fi
if [ "$PLATFORM" != "linux" ]; then
  echo "  Windows 는 자동 설치를 하지 않는다. 실행파일을 Git Bash 의 usr/bin 에 두거나" >&2
  echo "  그 디렉터리를 PATH 에 올릴 것(Windows PATH 가 아니라 그쪽이다)." >&2
  exit 1
fi

# apt 이름이 명령 이름과 다른 것만 옮긴다.
pkgs=()
for c in "${missing[@]}"; do
  case "$c" in
    python) pkgs+=("python-is-python3") ;;
    az)     echo "  az 는 apt 가 아니라 Microsoft 설치 스크립트로 깐다 — 건너뛴다." >&2 ;;
    *)      pkgs+=("$c") ;;
  esac
done
if [ ${#pkgs[@]} -gt 0 ]; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq "${pkgs[@]}"
fi

still=()
for c in "${want[@]}"; do
  command -v "$c" >/dev/null 2>&1 || still+=("$c")
done
[ ${#still[@]} -eq 0 ] || { echo "[setup_runner] 여전히 없음: ${still[*]}" >&2; exit 1; }
echo "[setup_runner] OK ($PLATFORM) — ${want[*]}"
