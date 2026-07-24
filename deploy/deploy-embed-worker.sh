#!/usr/bin/env bash
# 허브 자체호스팅 임베딩 워커(B) 설치/갱신 — 로컬(Git Bash)에서 실행.
# 원문을 서버 밖으로 보내지 않고, 허브 로컬 모델로 needs_embed 문서 벡터를 채운다.
# 배포 정본: Vinc-docs vinc/DEPLOYMENT.md(§4 인접). 사이드카는 /embed/pending·
# /embed/apply 를 노출해야 하므로 **이 스크립트 전에 deploy-hub.sh 로 사이드카를
# 먼저 재배포**할 것(엔드포인트 포함 빌드).
#
# 하는 일:
#   1) embed_worker.py 전송 → /opt/vinc/embed_worker.py
#   2) 전용 venv(/opt/vinc/embed-venv)에 sentence-transformers 설치(멱등)
#      — torch(CPU) ~수백MB + 모델(BAAI/bge-base-en-v1.5 768d) 최초 1회 다운로드
#   3) systemd service+timer 설치·활성(5분 간격 --once; idle 라운드는 GET 한 번)
#   4) 1회 즉시 발화 → 로그로 확인
set -euo pipefail

KEY="${VINC_KEY:-D:/Projects/AIProjects/vinc-project/vinc_key.pem}"
HOST="${VINC_HOST:-ubuntu@1.201.120.125}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "== 1/4 워커·유닛 전송 =="
scp -i "$KEY" "$HERE/embed_worker.py" "$HOST:/tmp/embed_worker.py"
scp -i "$KEY" "$HERE/vinc-embed-worker.service" "$HOST:/tmp/vinc-embed-worker.service"
scp -i "$KEY" "$HERE/vinc-embed-worker.timer" "$HOST:/tmp/vinc-embed-worker.timer"

echo "== 2/4 서버: venv 보장 + 설치 =="
ssh -i "$KEY" "$HOST" 'bash -s' <<'REMOTE'
set -euo pipefail
sudo install -o vinc -g vinc -m 755 /tmp/embed_worker.py /opt/vinc/embed_worker.py

# 전용 venv (사이드카 바이너리와 무관 — 임베딩 스택 전용)
if [ ! -x /opt/vinc/embed-venv/bin/python ]; then
  sudo -u vinc python3 -m venv /opt/vinc/embed-venv
fi
echo "-- sentence-transformers 설치(멱등, 수분 소요 첫 회) --"
sudo -u vinc /opt/vinc/embed-venv/bin/pip install -q --upgrade pip
sudo -u vinc /opt/vinc/embed-venv/bin/pip install -q "sentence-transformers"
sudo -u vinc /opt/vinc/embed-venv/bin/python -c "import sentence_transformers; print('   st', sentence_transformers.__version__)"

# HF 캐시 디렉토리(vinc 소유)
sudo -u vinc mkdir -p /home/vinc/.cache/huggingface

echo "-- systemd service+timer 설치 --"
sudo cp /tmp/vinc-embed-worker.service /etc/systemd/system/vinc-embed-worker.service
sudo cp /tmp/vinc-embed-worker.timer   /etc/systemd/system/vinc-embed-worker.timer
sudo systemctl daemon-reload
sudo systemctl enable --now vinc-embed-worker.timer

echo "-- 사이드카에 /embed/pending 있는지(엔드포인트 배포 확인) --"
TOKEN=$(sudo grep -hoP "(TOKEN|VINC_TOKEN)=\K\S+" /etc/vinc/sidecar.env | head -1)
code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:8787/embed/pending?limit=1" -H "Authorization: Bearer $TOKEN")
echo "   GET /embed/pending -> $code (200=배포됨 · 404=사이드카 재배포 필요: deploy-hub.sh 먼저)"

echo "-- 1회 즉시 발화 --"
sudo systemctl start vinc-embed-worker.service || true
sleep 2
sudo journalctl -u vinc-embed-worker.service -n 15 --no-pager --output=cat || true
REMOTE

echo "== 3/4 타이머 상태 =="
ssh -i "$KEY" "$HOST" "systemctl status vinc-embed-worker.timer --no-pager | head -6; systemctl list-timers vinc-embed-worker.timer --no-pager | head -3"

echo "== 4/4 완료 =="
echo "임베딩 워커 설치·활성. 5분 간격으로 needs_embed 문서 벡터를 채운다(원문 서버 밖 미전송)."
echo "수동 발화: ssh $HOST 'sudo systemctl start vinc-embed-worker.service'"
echo "로그:     ssh $HOST 'journalctl -u vinc-embed-worker.service -f'"
