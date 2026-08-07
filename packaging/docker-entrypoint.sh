#!/bin/sh
# 허브 컨테이너 엔트리포인트 — 사이드카(8787, 컨테이너 내부 전용)를 백그라운드로
# 띄우고 헬스가 ok 될 때까지 기다린 뒤, MCP HTTP(8790, 외부 ingress)를 메인
# 프로세스(exec)로 넘긴다. VM 배포의 두 systemd 유닛(vinc-sidecar·vinc-mcp)을
# 한 컨테이너 안에서 재현한다 — 데이터 디렉토리(Azure Files 마운트)를 공유해야
# 하고, MCP 가 사이드카를 127.0.0.1 로 호출하는 계약(--base)이 그대로이기 때문.
set -e

DATA_DIR="${VINC_DATA_DIR:-/data/db}"
: "${VINC_SIDECAR_TOKEN:?VINC_SIDECAR_TOKEN 필수 — 사이드카/MCP 내부 인증}"

# ── MCP OAuth 상태: 로컬 작업본 + 영속 볼륨 스냅샷 (2026-08-03) ──────────────────
# 배경(사고 둘을 연달아 겪고 정착한 모양이다):
#
#  ① 원래 상태 DB 가 `~/.vinc/mcp`(= 이 컨테이너에선 `/root/.vinc/mcp` = **이미지
#     레이어**)에 있었다. 영속 마운트는 `/data`(Azure Files) 하나뿐이라 리비전이
#     바뀔 때마다(api/ 변경마다 배포된다) OAuth 등록·토큰이 통째로 사라졌고, 커넥터가
#     캐시한 client_id 를 제시하면 `400 {"error":"unknown client_id"}` 로 영구히 막혔다.
#  ② 그래서 상태 DB 를 `/data/mcp` 로 옮겼더니 **SQLite 가 SMB 에서 기동 실패**했다
#     (`sqlite3.OperationalError: database is locked`, 첫 스키마 쓰기). Azure Files 는
#     SQLite 가 기대는 바이트 범위 락을 제대로 주지 않는다 — WAL 만의 문제가 아니다.
#     리비전이 Failed 로 떨어져 허브가 내려갔다(실측 장애).
#
# 결론: **읽고 쓰는 곳과 남기는 곳을 가른다.** SQLite 작업본은 로컬(락 정상)에 두고,
# 영속 볼륨에는 커밋된 DB 파일을 **복사**만 한다(SMB 가 확실히 지원하는 연산 —
# 임베딩 모델 캐시가 같은 볼륨에서 이미 그렇게 산다). 기동 시 작업본이 없으면
# 스냅샷에서 복원하므로 배포를 넘어 커넥터가 유지된다.
#
# env 로 넣지 않고 **여기서 기본값을 주는** 이유는 그대로다: 허브 런타임 env 는 손으로
# 넣는 값이라 앱을 재생성하면 조용히 빠진다(DEPLOYMENT.md §4.4, VINC_MCP_ISSUER 전례).
: "${VINC_MCP_STATE_DIR:=/var/lib/vinc-mcp}"          # 작업본 — 반드시 로컬
: "${VINC_MCP_STATE_SNAPSHOT:=${DATA_DIR%/*}/mcp/oauth.db}"   # 영속 — 파일 복사만
export VINC_MCP_STATE_DIR VINC_MCP_STATE_SNAPSHOT
# 둘 다 못 만들어도 **죽이지 않는다**: 상태 저장소는 편의이고 MCP 는 성립 조건이다.
# (api/cli.py 가 열기 실패 시 인메모리로 강등한다. ②의 교훈 — 저장소 문제로 서비스
# 전체를 내리지 않는다.) 실패는 로그로만 남긴다.
mkdir -p "$VINC_MCP_STATE_DIR" 2>/dev/null \
  || echo "[entrypoint] ⚠ 작업본 경로 생성 실패: $VINC_MCP_STATE_DIR" >&2
mkdir -p "$(dirname "$VINC_MCP_STATE_SNAPSHOT")" 2>/dev/null \
  || echo "[entrypoint] ⚠ 스냅샷 경로 생성 실패: $VINC_MCP_STATE_SNAPSHOT" >&2
echo "[entrypoint] MCP OAuth state: work=$VINC_MCP_STATE_DIR snapshot=$VINC_MCP_STATE_SNAPSHOT"
case "$VINC_MCP_STATE_SNAPSHOT" in
  "${DATA_DIR%/*}"/*) : ;;   # 영속 볼륨 안 — 정상
  *) echo "[entrypoint] ⚠ 스냅샷이 영속 볼륨(${DATA_DIR%/*}) 밖이다 —" \
          "배포마다 커넥터 재인가가 필요해진다." >&2 ;;
esac

# 쿼리 임베딩 창구(루프백). 사이드카는 이 주소로 물어 벡터 검색을 켠다 — 비어 있으면
# 시도조차 안 하고 FTS 로만 답한다(데스크탑의 정상 상태와 같다).
EMBED_PORT="${VINC_EMBED_PORT:-8791}"
if [ "${VINC_EMBED:-on}" = "on" ]; then
  export VINC_EMBED_ENCODE="http://127.0.0.1:${EMBED_PORT}"
fi

# 사이드카 PID 는 파일로 추적한다(변수가 아니라) — 아래 워치독이 재기동시켜도
# 이 스크립트 본 흐름(그리고 나중의 trap)이 **현재** pid 를 읽을 수 있어야 하는데,
# 워치독은 `( ) &` 서브셸이라 거기서 바꾼 셸 변수는 부모에 안 보인다(2026-08-03
# 사고 대응 — 그때는 사이드카가 죽으면 아무도 감시·재기동하지 않았다).
SIDECAR_PIDFILE="/tmp/vinc-sidecar.pid"

start_sidecar() {
  python -m api.cli serve --host 127.0.0.1 --port 8787 \
    --token "$VINC_SIDECAR_TOKEN" --data "$DATA_DIR" &
  echo $! > "$SIDECAR_PIDFILE"
}

# $1 = 최대 대기(초, 1회/s 폴링). 준비되면 0, 타임아웃이면 1.
wait_for_sidecar_health() {
  i=0
  while [ "$i" -lt "$1" ]; do
    if curl -sf http://127.0.0.1:8787/health | grep -q '"status":[[:space:]]*"ok"'; then
      return 0
    fi
    i=$((i + 1))
    sleep 1
  done
  return 1
}

start_sidecar

# 사이드카가 죽으면 컨테이너 전체를 내린다(둘 중 하나만 죽어 반쪽 상태로
# 떠 있는 것을 방지 — Container Apps 가 재시작을 판단할 신호가 된다). pidfile 로
# 읽으므로 아래 워치독이 재기동시킨 뒤에도 항상 **현재** 사이드카를 겨눈다.
trap 'kill "$(cat "$SIDECAR_PIDFILE" 2>/dev/null)" 2>/dev/null' TERM INT

echo "[entrypoint] waiting for sidecar health..."
if wait_for_sidecar_health 30; then
  echo "[entrypoint] sidecar ready"
else
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
# ⚠ **워커는 감시해야 한다.** 2026-08-05~07 실측: 워커가 기동 직후(`loopback ok ·
# pending=133`) 사라졌고 38시간 동안 아무도 몰랐다. 파이썬 예외였다면 워커 자신이
# 로그를 남겼겠지만 흔적이 0줄이었다 — 즉 SIGKILL(커널 cgroup OOM) 또는 SIGSEGV 다.
# 둘 다 죽는 프로세스가 아무것도 못 남기므로 **부모가 종료코드를 읽어 주는 수밖에 없다**.
# 그동안 컨테이너는 Healthy · /health 는 200 · 사이드카도 정상이었다. 이 리포가 반복해
# 겪는 그 모양(기동 정상 · 헬스 200 · 기능만 죽음)의 새 사례다.
EMBED_PIDFILE="/tmp/vinc-embed.pid"
EMBED_SUP_PID=""
if [ "${VINC_EMBED:-on}" = "on" ]; then
  (
    attempt=0
    while true; do
      VINC_SIDECAR="http://127.0.0.1:8787" \
        python packaging/embed_worker.py --interval "${VINC_EMBED_INTERVAL:-300}" \
          --encode-port "$EMBED_PORT" &
      cpid=$!
      echo "$cpid" > "$EMBED_PIDFILE"
      echo "[entrypoint] embed worker started (pid=$cpid, interval=${VINC_EMBED_INTERVAL:-300}s," \
           "encode=127.0.0.1:$EMBED_PORT, attempt=$((attempt + 1)))"
      # ⚠ `set -e` 아래서는 `wait` 가 비영을 돌려주는 순간 이 서브셸이 그대로 죽는다
      # (그러면 감시자가 사라져 원래 문제로 되돌아간다). `|| rc=$?` 로 감싸 상태만
      # 받아 온다 — 0 이면 대입이 일어나지 않으므로 초기값 0 이 남는다.
      rc=0
      wait "$cpid" || rc=$?
      attempt=$((attempt + 1))
      # 128+N = 신호 N 으로 죽었다는 뜻이다. 사인을 말로 옮겨 둔다 — 다음 사람이
      # 숫자를 찾아보지 않아도 되게.
      case "$rc" in
        137) why="OOM(SIGKILL, 137) — 컨테이너 메모리 한도 초과로 커널이 죽였다" ;;
        139) why="SIGSEGV(139) — 네이티브 크래시(onnxruntime·확장 의심)" ;;
        143) why="SIGTERM(143) — 정상 종료 신호" ;;
        0)   why="종료코드 0(루프가 스스로 끝났다 — 상주 모드에서는 비정상)" ;;
        *)   why="종료코드 $rc" ;;
      esac
      echo "[entrypoint] ⚠ embed worker 종료: $why" >&2
      # `[ ... ] && exit 0` 로 쓰면 조건이 거짓일 때 목록 전체가 비영이 되어 `set -e`
      # 가 서브셸을 죽인다. if 로 적는다.
      if [ "$rc" = "143" ]; then
        exit 0
      fi
      if [ "$attempt" -ge "${VINC_EMBED_MAX_RESTARTS:-3}" ]; then
        echo "[entrypoint] ⚠ embed worker 재기동 상한($attempt) 도달 — 감시 중단." \
             "벡터 검색은 꺼진 채로 남고 /health 의 embed.pending 이 줄지 않는다." >&2
        exit 1
      fi
      # 재기동 간격을 넉넉히 둔다: 사인이 OOM 이면 즉시 재시도가 같은 고점을 다시
      # 쳐서 이번엔 사이드카나 MCP 가 대신 죽을 수 있다(킬러는 RSS 가 큰 쪽을 고른다).
      sleep "${VINC_EMBED_RESTART_BACKOFF:-120}"
    done
  ) &
  EMBED_SUP_PID=$!
  # 워커는 **죽어도 컨테이너를 내리지 않는다** — 부가 기능이고, 여기서 컨테이너를
  # 재시작시키면 단일 writer 락 회수까지 얽혀 MCP 가용성이 배경 작업에 인질이 된다.
  trap 'kill "$(cat "$SIDECAR_PIDFILE" 2>/dev/null)" "$(cat "$EMBED_PIDFILE" 2>/dev/null)" \
        "$EMBED_SUP_PID" 2>/dev/null' TERM INT
fi

# ── 사이드카 워치독 (2026-08-03 사고 대응) ───────────────────────────────────
# 그 사고 때 사이드카가 몇 분간 응답 불가였는데(원인은 로그에 남지 않음 — 이번에
# 예외 로깅을 추가했으니 다음엔 남는다) 아무도 감시·재기동하지 않았다: 컨테이너의
# PID 1(아래 exec 로 넘어가는 MCP HTTP)은 계속 살아 `/health` 도 200 을 줬으므로
# Container Apps 관점에서는 "정상"이었고, 사이드카가 스스로 돌아올 때까지(혹은
# 다음 배포까지) 그래프 read/write 전부가 조용히 막혀 있었다.
#
# 5초마다 생사만 확인(kill -0, 가볍다) — 죽었으면 재기동 후 헬스를 다시 기다린다.
# 5회 연속 실패하면 포기하고 워치독만 조용히 멈춘다(무한 재기동이 Azure Files 락
# 회수를 더 어렵게 만들 수 있다 — hub-deploy.yml 의 "단일 writer" 교훈과 같은
# 이유). 그래도 MCP HTTP 는 계속 떠 있으므로 `/health` 의 `sidecar.reachable`
# 진단으로는 계속 드러난다 — 사람이 개입할 신호는 남는다.
(
  restart_count=0
  while true; do
    sleep 5
    pid="$(cat "$SIDECAR_PIDFILE" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      restart_count=0
      continue
    fi
    restart_count=$((restart_count + 1))
    if [ "$restart_count" -gt 5 ]; then
      echo "[entrypoint] ⚠ sidecar 재기동 5회 연속 실패 — watchdog 중단(수동 개입 필요)" >&2
      exit 1
    fi
    echo "[entrypoint] ⚠ sidecar(pid=${pid:-?}) 무응답 — 재기동 시도 $restart_count/5" >&2
    # OS 가 죽은 프로세스의 파일 핸들·락을 정리할 시간(Azure Files=SMB 는 로컬보다
    # 늦게 반영될 수 있다 — 즉시 재기동하면 새 프로세스가 락을 또 못 잡는다).
    sleep 2
    start_sidecar
    if wait_for_sidecar_health 30; then
      echo "[entrypoint] sidecar 재기동 성공(pid=$(cat "$SIDECAR_PIDFILE" 2>/dev/null))"
      restart_count=0
    else
      echo "[entrypoint] ⚠ sidecar 재기동 후에도 헬스 실패(pid=$(cat "$SIDECAR_PIDFILE" 2>/dev/null))" >&2
    fi
  done
) &
WATCHDOG_PID=$!
trap 'kill "$(cat "$SIDECAR_PIDFILE" 2>/dev/null)" "$(cat "$EMBED_PIDFILE" 2>/dev/null)" \
      "$EMBED_SUP_PID" "$WATCHDOG_PID" 2>/dev/null' TERM INT

MCP_ARGS="--host 0.0.0.0 --port 8790 --base http://127.0.0.1:8787 --token $VINC_SIDECAR_TOKEN --no-spawn"
if [ -n "$VINC_MCP_TOKEN" ]; then
  MCP_ARGS="$MCP_ARGS --auth-token $VINC_MCP_TOKEN"
fi
if [ -n "$VINC_MCP_ISSUER" ]; then
  MCP_ARGS="$MCP_ARGS --oauth --issuer $VINC_MCP_ISSUER"
fi

# shellcheck disable=SC2086
exec python -m api.cli mcp serve-http $MCP_ARGS
