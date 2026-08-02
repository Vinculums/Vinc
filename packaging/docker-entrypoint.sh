#!/bin/sh
# 허브 컨테이너 엔트리포인트 — 사이드카(8787, 컨테이너 내부 전용)를 백그라운드로
# 띄우고 헬스가 ok 될 때까지 기다린 뒤, MCP HTTP(8790, 외부 ingress)를 메인
# 프로세스(exec)로 넘긴다. VM 배포의 두 systemd 유닛(vinc-sidecar·vinc-mcp)을
# 한 컨테이너 안에서 재현한다 — 데이터 디렉토리(Azure Files 마운트)를 공유해야
# 하고, MCP 가 사이드카를 127.0.0.1 로 호출하는 계약(--base)이 그대로이기 때문.
set -e

DATA_DIR="${VINC_DATA_DIR:-/data/db}"
: "${VINC_SIDECAR_TOKEN:?VINC_SIDECAR_TOKEN 필수 — 사이드카/MCP 내부 인증}"

# ── MCP OAuth 상태 경로 — **영속 볼륨에 둔다**(2026-08-03 사고 수정) ──────────────
# `vinc_mcp/store.py` 는 미설정 시 `~/.vinc/mcp` 로 폴백하는데, 이 컨테이너는 root 로
# 돌므로 그게 `/root/.vinc/mcp` = **이미지 레이어**다. 영속 마운트는 `/data`(Azure
# Files) 하나뿐이라, 리비전이 바뀔 때마다(=api/ 변경마다 배포된다) OAuth 클라이언트
# 등록·토큰이 통째로 사라졌다. 증상: 재연결한 커넥터가 캐시해 둔 client_id 를 그대로
# 제시하는데 서버엔 기록이 없어 `/authorize` 가 `400 {"error":"unknown client_id"}`.
# 클라이언트는 스스로 재등록하지 않으므로 한 번 어긋나면 계속 막힌다.
#
# 원래 의도는 영속이었다(`api/cli.py` "재기동에도 커넥터 유지", 프로덕션 경로
# `/var/lib/vinc-mcp`). 그 경로는 **가비아 VM + systemd StateDirectory** 시절 것이고
# Azure Container Apps 이관(2026-07-28/29) 때 대체 설정이 안 들어가 의도만 남았다.
#
# env 로 넣지 않고 **여기서 기본값을 준다**: 허브 런타임 env 는 손으로 넣는 값이라
# 앱을 재생성하면 조용히 빠진다(DEPLOYMENT.md §4.4 가 경고하는 실패 모드이고,
# VINC_MCP_ISSUER 가 실제로 그렇게 빠졌다). 기본값을 코드에 두면 그 경로가 막힌다.
: "${VINC_MCP_STATE_DIR:=/data/mcp}"
export VINC_MCP_STATE_DIR
# 여기서 못 만들면 뒤에서 SQLite 가 열리다 죽는다 — `set -e` 의 말 없는 종료보다
# 사유를 적고 죽는 편이 낫다(이 사고의 급소가 "조용함"이었다).
mkdir -p "$VINC_MCP_STATE_DIR" || {
  echo "[entrypoint] MCP 상태 경로를 만들 수 없다: $VINC_MCP_STATE_DIR" >&2
  echo "[entrypoint] 볼륨 마운트를 확인할 것(DEPLOYMENT.md §4.6)." >&2
  exit 1
}
# 어디에 저장하는지 로그로 남긴다 — 영속 여부는 /health 에 안 드러나므로(이 사고의
# 급소가 그것이다) 최소한 기동 로그에서는 눈으로 확인할 수 있어야 한다.
case "$VINC_MCP_STATE_DIR" in
  "${DATA_DIR%/*}"/*) echo "[entrypoint] MCP OAuth state: $VINC_MCP_STATE_DIR (persistent)" ;;
  *) echo "[entrypoint] ⚠ MCP OAuth state: $VINC_MCP_STATE_DIR — 영속 볼륨(${DATA_DIR%/*}) 밖이다." \
          "재시작마다 커넥터 재인가가 필요해진다." >&2 ;;
esac

# 쿼리 임베딩 창구(루프백). 사이드카는 이 주소로 물어 벡터 검색을 켠다 — 비어 있으면
# 시도조차 안 하고 FTS 로만 답한다(데스크탑의 정상 상태와 같다).
EMBED_PORT="${VINC_EMBED_PORT:-8791}"
if [ "${VINC_EMBED:-on}" = "on" ]; then
  export VINC_EMBED_ENCODE="http://127.0.0.1:${EMBED_PORT}"
fi

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

# 임베딩 워커 — 가비아 시절 systemd timer(5분 간격)의 컨테이너 등가물. 이게 없으면
# 웹 편집·팩 임포트·파일 드롭으로 들어온 문서가 needs_embed=1 로 남아 FTS 만 되고
# 벡터가 빈다(에러 없이 의미검색만 열화 — Azure 이관 때 실제로 이 상태였다).
# 사이드카 헬스 뒤에 띄운다: 워커의 첫 동작이 루프백 GET 이므로 순서가 뒤바뀌면
# 첫 라운드가 무의미하게 실패한다.
# VINC_EMBED=off 로 재빌드 없이 끌 수 있게 둔다 — 배경 작업이 전경을 방해하는
# 상황에서 env 하나로 물러설 길이 필요하다.
EMBED_PID=""
if [ "${VINC_EMBED:-on}" = "on" ]; then
  VINC_SIDECAR="http://127.0.0.1:8787" \
    python packaging/embed_worker.py --interval "${VINC_EMBED_INTERVAL:-300}" \
      --encode-port "$EMBED_PORT" &
  EMBED_PID=$!
  echo "[entrypoint] embed worker started (pid=$EMBED_PID, interval=${VINC_EMBED_INTERVAL:-300}s,"\
       "encode=127.0.0.1:$EMBED_PORT)"
  # 워커는 **죽어도 컨테이너를 내리지 않는다** — 부가 기능이고, 여기서 컨테이너를
  # 재시작시키면 단일 writer 락 회수까지 얽혀 MCP 가용성이 배경 작업에 인질이 된다.
  trap 'kill "$SIDECAR_PID" "$EMBED_PID" 2>/dev/null' TERM INT
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
