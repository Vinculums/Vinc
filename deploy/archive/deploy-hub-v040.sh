#!/usr/bin/env bash
# 허브 배포 v0.4.0 — 그래프 구조 수리 9건(커밋 1c26e7c, 태그 v0.4.0).
# 개방층 라이브 포팅(F1)·Bridge owner v2 마이그레이션(F5)·ABOUT(F4) 포함.
# 로컬(Git Bash)에서 실행. 교체 전 ① 무중단 DB 스냅샷(HTTP /backup — v2 ALTER 가
# 라이브 스토어에 닿으므로) ② 바이너리 백업. 롤백 가능.
set -euo pipefail

KEY="D:/Projects/AIProjects/vinc-project/vinc_key.pem"
HOST="ubuntu@1.201.120.125"
ARCHIVE="D:/Projects/AIProjects/vinc-project/vinc-hub-v040.tar.gz"

echo "== 1/2 아카이브 전송 =="
scp -i "$KEY" "$ARCHIVE" "$HOST:/home/ubuntu/vinc-hub-v040.tar.gz"

echo "== 2/2 서버: 스냅샷 → 추출 → 빌드 → 백업·설치 → 재시작(자동 마이그레이션 v2) =="
ssh -i "$KEY" "$HOST" 'bash -s' <<'REMOTE'
set -euo pipefail

echo "-- 무중단 DB 스냅샷(교체 전 — v2 ALTER 대비) --"
sudo /opt/vinc/vinc-backup.sh || echo "   (스냅샷 스크립트 실패 — 계속하되 기록)"

BUILD=/home/ubuntu/vinc-build-v040
rm -rf "$BUILD"; mkdir -p "$BUILD"
tar xzf /home/ubuntu/vinc-hub-v040.tar.gz -C "$BUILD"
cd "$BUILD"

echo "-- PyInstaller 빌드(수 분) --"
/home/ubuntu/.build-venv/bin/pyinstaller -y packaging/sidecar.spec >/tmp/hub-v040-build.log 2>&1 \
  || { echo "빌드 실패 — /tmp/hub-v040-build.log"; tail -20 /tmp/hub-v040-build.log; exit 1; }

echo "-- 스모크(새 바이너리) --"
./dist/vinc-sidecar --help >/dev/null && echo "   binary ok"

echo "-- 현 바이너리 백업 후 교체 --"
TS=$(date +%s)
sudo cp -a /opt/vinc/vinc-sidecar "/opt/vinc/vinc-sidecar.bak.$TS"
echo "   backup: /opt/vinc/vinc-sidecar.bak.$TS"
sudo install -o vinc -g vinc -m 755 dist/vinc-sidecar /opt/vinc/vinc-sidecar

echo "-- 재시작(sidecar → mcp): 기동 시 provision→migrate 가 v2 ALTER 적용 --"
sudo systemctl restart vinc-sidecar
sudo systemctl restart vinc-mcp
sleep 4
systemctl is-active vinc-sidecar vinc-mcp
echo "허브 v0.4.0 배포 완료. 롤백: sudo install -o vinc -g vinc -m755 /opt/vinc/vinc-sidecar.bak.$TS /opt/vinc/vinc-sidecar && sudo systemctl restart vinc-sidecar vinc-mcp"
echo "⚠ vinc-mcp 재시작으로 OAuth 인메모리 세션 초기화 — claude.ai 커넥터 재연결 필요"
REMOTE
echo ""
echo "완료. 이어서 읽기전용 SSH 로 마이그레이션 v2·2-owner 격리를 검증한다."
