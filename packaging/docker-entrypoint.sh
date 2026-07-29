#!/bin/sh
# 허브 컨테이너 엔트리포인트 — 사이드카(8787, 컨테이너 내부 전용)를 백그라운드로
# 띄우고 헬스가 ok 될 때까지 기다린 뒤, MCP HTTP(8790, 외부 ingress)를 메인
# 프로세스(exec)로 넘긴다. VM 배포의 두 systemd 유닛(vinc-sidecar·vinc-mcp)을
# 한 컨테이너 안에서 재현한다 — 데이터 디렉토리(Azure Files 마운트)를 공유해야
# 하고, MCP 가 사이드카를 127.0.0.1 로 호출하는 계약(--base)이 그대로이기 때문.
set -e

DATA_DIR="${VINC_DATA_DIR:-/data/db}"
: "${VINC_SIDECAR_TOKEN:?VINC_SIDECAR_TOKEN 필수 — 사이드카/MCP 내부 인증}"

python -m api.cli serve --host 127.0.0.1 --port 8787 \
  --token "$VINC_SIDECAR_TOKEN" --data "$DATA_DIR" &
SIDECAR_PID=$!

# 사이드카가 죽으면 컨테이너 전체를 내린다(둘 중 하나만 죽어 반쪽 상태로
# 떠 있는 것을 방지 — Container Apps 가 재시작을 판단할 신호가 된다).
trap 'kill "$SIDECAR_PID" 2>/dev/null' TERM INT

echo "[entrypoint] waiting for sidecar health..."
i=0
while [ "$i" -lt 30 ]; do
  if curl -sf http://127.0.0.1:8787/health | grep -q '"status":[[:space:]]*"ok"'; then
    echo "[entrypoint] sidecar ready"
    break
  fi
  i=$((i + 1))
  sleep 1
done
if [ "$i" -ge 30 ]; then
  echo "[entrypoint] sidecar health check timed out" >&2
  exit 1
fi

MCP_ARGS="--host 0.0.0.0 --port 8790 --base http://127.0.0.1:8787 --token $VINC_SIDECAR_TOKEN --no-spawn"
if [ -n "$VINC_MCP_TOKEN" ]; then
  MCP_ARGS="$MCP_ARGS --auth-token $VINC_MCP_TOKEN"
fi
if [ -n "$VINC_MCP_ISSUER" ]; then
  MCP_ARGS="$MCP_ARGS --oauth --issuer $VINC_MCP_ISSUER"
fi

# shellcheck disable=SC2086
exec python -m api.cli mcp serve-http $MCP_ARGS
