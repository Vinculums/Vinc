#!/usr/bin/env bash
# 허브 배포 — SEC-3(쓰기 owner 하드닝) + 웹 Vinc Phase 1(그래프 프록시). 커밋 b5010da.
# 로컬(Git Bash)에서 실행. 현 바이너리 백업 후 교체 → 롤백 가능.
# 사이드카 빌드(sidecar.spec)는 Python 만 번들 → app/ 프론트 변경은 무관.
set -euo pipefail

KEY="D:/Projects/AIProjects/vinc-project/vinc_key.pem"
HOST="ubuntu@1.201.120.125"
ARCHIVE="D:/Projects/AIProjects/vinc-project/vinc-hub.tar.gz"

echo "== 1/2 아카이브 전송 =="
scp -i "$KEY" "$ARCHIVE" "$HOST:/home/ubuntu/vinc-hub.tar.gz"

echo "== 2/2 서버: 추출 → 빌드 → 백업·설치 → 재시작 =="
ssh -i "$KEY" "$HOST" 'bash -s' <<'REMOTE'
set -euo pipefail
BUILD=/home/ubuntu/vinc-build-hub
rm -rf "$BUILD"; mkdir -p "$BUILD"
tar xzf /home/ubuntu/vinc-hub.tar.gz -C "$BUILD"
cd "$BUILD"

echo "-- PyInstaller 빌드(수 분) --"
/home/ubuntu/.build-venv/bin/pyinstaller -y packaging/sidecar.spec >/tmp/hub-build.log 2>&1 \
  || { echo "빌드 실패 — /tmp/hub-build.log"; tail -20 /tmp/hub-build.log; exit 1; }

echo "-- 스모크(새 바이너리) --"
./dist/vinc-sidecar --help >/dev/null && echo "   binary ok"

echo "-- 현 바이너리 백업 후 교체 --"
TS=$(date +%s)
sudo cp -a /opt/vinc/vinc-sidecar "/opt/vinc/vinc-sidecar.bak.$TS"
echo "   backup: /opt/vinc/vinc-sidecar.bak.$TS"
sudo install -o vinc -g vinc -m 755 dist/vinc-sidecar /opt/vinc/vinc-sidecar

echo "-- 재시작(sidecar → mcp) --"
sudo systemctl restart vinc-sidecar
sudo systemctl restart vinc-mcp
sleep 3
systemctl is-active vinc-sidecar vinc-mcp
echo "허브 배포 완료(SEC-3+Phase1). 롤백: sudo install -o vinc -g vinc -m755 /opt/vinc/vinc-sidecar.bak.$TS /opt/vinc/vinc-sidecar && sudo systemctl restart vinc-sidecar vinc-mcp"
REMOTE
echo ""
echo "완료. 이제 Claude 가 읽기전용 SSH 로 2-owner 격리 + graph-owner 프록시를 검증합니다."
