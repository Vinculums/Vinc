#!/usr/bin/env bash
# 허브(사이드카 + MCP 어댑터) 배포 — 로컬(Git Bash)에서 실행.
# 배포 절차 정본: Vinc-docs vinc/DEPLOYMENT.md §4.
#
# ⚠ 허브는 **CI 산출물이 아니다.** 데스크탑 릴리스 CI(release.yml)는 GitHub Releases 용
# 설치본만 만들고 이 서버엔 손대지 않는다. 허브 바이너리는 **서버에서 직접 PyInstaller 로
# 빌드**한다 — 그래서 이 스크립트가 필요하다.
#
# 이 스크립트가 기존 deploy-hub*.sh(2026-07 archive/)를 대체한다. 옛 스크립트들은
#   ① 특정 과거 커밋에 묶인 일회성이고
#   ② kuzu 시절 venv(/home/ubuntu/.build-venv)에 의존해 **지금 돌리면 실패**한다
#      (v0.5.3 에서 kuzu 제거·ladybug 단일 전환).
# 여기서는 빌드 환경을 **매 실행 멱등하게 보장**해 그 함정을 없앤다.
set -euo pipefail

KEY="${VINC_KEY:-D:/Projects/AIProjects/vinc-project/vinc_key.pem}"
HOST="${VINC_HOST:-ubuntu@1.201.120.125}"
REPO="${VINC_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
ARCHIVE="/tmp/vinc-hub.tar.gz"

# ── V4: 배포 ↔ git 이탈 경고 ────────────────────────────────────────────────
if git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1; then
  DIRTY=$(git -C "$REPO" status --porcelain | wc -l | tr -d ' ')
  if [ "$DIRTY" != "0" ]; then
    echo "⚠  경고: Vinc 작업트리에 미커밋 변경 $DIRTY 건 — 이대로 배포하면 라이브를 재현하는 커밋이 없다."
    git -C "$REPO" status --short | head -20 | sed 's/^/     /'
    printf "   계속하려면 Enter, 중단하려면 Ctrl-C … "; read -r _
  else
    echo "✓ Vinc 작업트리 클린 ($(git -C "$REPO" rev-parse --short HEAD))"
  fi
fi

echo "== 1/4 소스 번들 =="
# 사이드카 빌드는 Python 만 필요 — app/(프론트)·.git·빌드 산출물 제외.
tar czf "$ARCHIVE" -C "$(dirname "$REPO")" \
  --exclude="$(basename "$REPO")/app" \
  --exclude="$(basename "$REPO")/.git" \
  --exclude="$(basename "$REPO")/dist" \
  --exclude="$(basename "$REPO")/build" \
  --exclude='__pycache__' --exclude='.pytest_cache' --exclude='*.pyc' \
  "$(basename "$REPO")"
echo "   $(du -h "$ARCHIVE" | cut -f1)  $ARCHIVE"

echo "== 2/4 전송 =="
scp -i "$KEY" "$ARCHIVE" "$HOST:/home/ubuntu/vinc-hub.tar.gz"

echo "== 3/4 서버: 빌드 환경 보장 → 빌드 → 스모크 → 백업·설치 → 재시작 =="
ssh -i "$KEY" "$HOST" 'bash -s' <<'REMOTE'
set -euo pipefail
BUILD=/home/ubuntu/vinc-build-hub
VENV=/home/ubuntu/.build-venv-lb          # ladybug 전용(구 .build-venv 는 kuzu — 건드리지 않는다)

echo "-- 빌드 환경 보장(멱등) --"
[ -d "$VENV" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q ladybug pyyaml pyinstaller
"$VENV/bin/python" -c "import ladybug, PyInstaller" || { echo "빌드 의존성 미비"; exit 1; }
echo "   venv ok"

# G0(오프라인 확장 번들): sidecar.spec 이 ~/.lbdb/extension 에서 vector/fts/algo 를 찾아
# datas 로 동봉한다. 3개가 없으면 spec 이 SystemExit 로 빌드를 막는다. 멱등 시드.
echo "-- 확장 시드(G0) --"
"$VENV/bin/python" - <<'PY'
import ladybug, tempfile, os
c = ladybug.Connection(ladybug.Database(os.path.join(tempfile.mkdtemp(), "s")))
for e in ("vector", "fts", "algo"):
    c.execute("INSTALL " + e)
print("   extensions seeded")
PY

rm -rf "$BUILD"; mkdir -p "$BUILD"
tar xzf /home/ubuntu/vinc-hub.tar.gz -C "$BUILD" --strip-components=1
cd "$BUILD"

echo "-- PyInstaller 빌드(수 분) --"
"$VENV/bin/python" -m PyInstaller --noconfirm --clean packaging/sidecar.spec \
  >/tmp/hub-build.log 2>&1 || { echo "빌드 실패 — /tmp/hub-build.log"; tail -25 /tmp/hub-build.log; exit 1; }

# ── 교체 전 스모크: 격리 포트·격리 데이터로 **실제 기동**을 확인한다. ──────────
# `--help` 만으로는 부족하다 — v0.5.1/v0.5.2 가 그렇게 통과해놓고 그래프 스토어를 전혀
# 열지 못한 채 발행됐다(DEPLOYMENT.md §1.4). status=="ok" 를 명시 검사한다.
echo "-- 교체 전 스모크(격리 실행) --"
SMOKE_DATA=$(mktemp -d)/db
./dist/vinc-sidecar serve --host 127.0.0.1 --port 18787 --token smoke --data "$SMOKE_DATA" \
  >/tmp/hub-smoke.log 2>&1 &
SPID=$!
ok=""
for _ in $(seq 1 30); do
  if curl -sf http://127.0.0.1:18787/health 2>/dev/null | grep -q '"status": *"ok"'; then ok=1; break; fi
  sleep 0.5
done
kill "$SPID" 2>/dev/null || true
[ -n "$ok" ] || { echo "❌ 새 바이너리가 스토어를 못 연다 — 교체 중단"; cat /tmp/hub-smoke.log; exit 1; }
echo "   smoke ok (store opens)"

echo "-- 교체 전 무중단 DB 스냅샷 --"
# 스키마 마이그레이션이 기동 시 자동 적용될 수 있으므로 교체 전에 남긴다.
sudo /opt/vinc/vinc-backup.sh >/tmp/hub-backup.log 2>&1 \
  && echo "   snapshot ok" || echo "   ⚠ 스냅샷 실패(계속 진행 — /tmp/hub-backup.log)"

echo "-- 현 바이너리 백업 후 교체 --"
TS=$(date +%Y%m%d%H%M%S)
sudo cp -a /opt/vinc/vinc-sidecar "/opt/vinc/vinc-sidecar.bak-$TS"
sudo install -o vinc -g vinc -m 755 dist/vinc-sidecar /opt/vinc/vinc-sidecar
echo "   backup: /opt/vinc/vinc-sidecar.bak-$TS"

echo "-- 재시작(sidecar → mcp) --"
sudo systemctl restart vinc-sidecar
sudo systemctl restart vinc-mcp
sleep 4

# ── 라이브 검증 + 실패 시 자동 롤백 ────────────────────────────────────────
# 실제로 겪은 사고: ladybug 전용 빌드를 kuzu 데이터 허브에 올려 "no reachable store" 로
# 즉시 장애(DEPLOYMENT.md §4.3). 그때 수동 롤백으로 복구했다 — 그 판단을 자동화한다.
LIVE=$(curl -s http://127.0.0.1:8787/health 2>/dev/null || true)
if echo "$LIVE" | grep -q '"status": *"ok"'; then
  echo "   ✓ 라이브 /health ok"
  systemctl is-active vinc-sidecar vinc-mcp
  echo "허브 배포 완료. 롤백이 필요하면:"
  echo "  sudo install -o vinc -g vinc -m755 /opt/vinc/vinc-sidecar.bak-$TS /opt/vinc/vinc-sidecar && sudo systemctl restart vinc-sidecar vinc-mcp"
else
  echo "❌ 라이브 /health 비정상 → 자동 롤백한다"
  echo "   응답: $LIVE"
  sudo install -o vinc -g vinc -m 755 "/opt/vinc/vinc-sidecar.bak-$TS" /opt/vinc/vinc-sidecar
  sudo systemctl restart vinc-sidecar; sudo systemctl restart vinc-mcp; sleep 3
  echo "   롤백 후 /health: $(curl -s http://127.0.0.1:8787/health 2>/dev/null)"
  echo "   원인을 확인할 것 — 스토어 포맷 불일치(§4.3)가 가장 흔하다."
  exit 1
fi
echo "⚠ vinc-mcp 재시작으로 OAuth 인메모리 세션 초기화 — claude.ai 커넥터 재연결이 필요할 수 있다."
REMOTE

echo "== 4/4 외부 검증 =="
M=$(curl -s -o /dev/null -w '%{http_code}' https://mcp.vincs.app 2>/dev/null || echo "?")
echo "   https://mcp.vincs.app -> $M (501=라우트 없음이지만 도달 정상)"
echo ""
echo "허브 배포 완료. 배포한 내용을 아직 커밋하지 않았다면 지금 커밋할 것."
