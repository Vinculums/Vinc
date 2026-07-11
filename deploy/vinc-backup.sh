#!/usr/bin/env bash
# Vinc 야간 백업 — 서버 프로세스 내부 스냅샷(무중단·무락).
#
# 사이드카가 소유한 단일 Database 에서 CHECKPOINT+복사를 수행하는 HTTP
# `POST /backup`(잡 큐 직렬화, R4)을 호출한다. CLI `vinc-sidecar backup` 은 별도
# 프로세스로 kuzu 를 다시 열어 **서비스 기동 중 배타 락으로 실패**하므로, 야간
# 백업의 정본 경로는 이 HTTP 엔드포인트다(서비스 정지 불필요 = 무중단).
#
# root 로 실행한다 — 세션 토큰이 /etc/vinc/sidecar.env(권한 600)에 있다.
#   사용: vinc-backup.sh [OUT_DIR]   (기본 OUT = ~vinc/.vinc/backups/YYYYMMDD)
set -euo pipefail

ENV_FILE="${VINC_SIDECAR_ENV:-/etc/vinc/sidecar.env}"
PORT="${VINC_SIDECAR_PORT:-8787}"
OUT="${1:-/home/vinc/.vinc/backups/$(date +%Y%m%d)}"

# shellcheck disable=SC1090
source "$ENV_FILE"
BASE="http://127.0.0.1:${PORT}"
AUTH="Authorization: Bearer ${VINC_SIDECAR_TOKEN}"

resp=$(curl -fsS -X POST -H "$AUTH" -H 'Content-Type: application/json' \
       "$BASE/backup" -d "{\"out\":\"$OUT\"}")
job=$(printf '%s' "$resp" | grep -oP '"job_id":\s*"\K[^"]+' | head -1)
[ -n "$job" ] || { echo "backup submit failed: $resp" >&2; exit 1; }

# 비동기 잡 완료 폴링(최대 ~120s)
for _ in $(seq 1 120); do
  st=$(curl -fsS -H "$AUTH" "$BASE/jobs/$job" | grep -oP '"status":\s*"\K[^"]+' | head -1)
  case "$st" in
    done)  echo "$(date -Is) backup done → $OUT"; exit 0 ;;
    error) echo "$(date -Is) backup FAILED:" >&2
           curl -fsS -H "$AUTH" "$BASE/jobs/$job" >&2; echo >&2; exit 1 ;;
  esac
  sleep 1
done
echo "$(date -Is) backup timed out (job=$job)" >&2
exit 1
