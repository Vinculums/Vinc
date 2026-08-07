#!/bin/sh
# 허브 컨테이너 엔트리포인트 — 사이드카(8787, 컨테이너 내부 전용)를 백그라운드로
# 띄우고 헬스가 ok 될 때까지 기다린 뒤, MCP HTTP(8790, 외부 ingress)를 메인
# 프로세스(exec)로 넘긴다. VM 배포의 두 systemd 유닛(vinc-sidecar·vinc-mcp)을
# 한 컨테이너 안에서 재현한다 — 데이터 디렉토리(Azure Files 마운트)를 공유해야
# 하고, MCP 가 사이드카를 127.0.0.1 로 호출하는 계약(--base)이 그대로이기 때문.
set -e

# ── 역할 분기 (2026-08-07) ──────────────────────────────────────────────────
# 같은 이미지가 두 앱으로 뜬다.
#   VINC_ROLE=encode  →  `vinc-encode`: 모델만 든다. 2Gi · minReplicas=0
#   (미설정)          →  `vinc-hub`   : 사이드카 + MCP + 모델을 안 드는 얇은 워커
#
# 왜 갈랐나: 허브(1Gi)에서 ONNX 적재가 rss 646MB 에 닿아 커널이 워커를 SIGKILL(137)
# 했다. 3회 재기동 전부 같은 지점 — 코드가 아니라 자원 문제다. 허브를 2Gi 로 올리면
# 상시 과금이 배증하므로, 모델만 떼어 **쓸 때만 깨는** 앱으로 보낸다.
#
# ⚠ encode 역할은 사이드카도 MCP 도 띄우지 않는다 — 그러니 **DATA_DIR 을 안 만진다.**
#   임베디드 DB 는 단일 writer 라, 이 앱이 실수로 스토어를 열면 허브가 못 연다.
if [ "${VINC_ROLE:-}" = "encode" ]; then
  : "${VINC_EMBED_PORT:=8791}"
  : "${VINC_EMBED_BIND:=0.0.0.0}"
  export VINC_EMBED_PORT VINC_EMBED_BIND
  echo "[entrypoint] role=encode — 인코딩 서비스만 (사이드카·MCP 없음)"
  # 모델은 **첫 요청에서** 올라간다(지연 로드). 프로브만 받고 도로 자는 리비전이
  # 646MB 를 올리지 않도록.
  exec python packaging/embed_worker.py --serve-only --encode-port "$VINC_EMBED_PORT"
fi

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

# 쿼리 임베딩 창구. 사이드카는 이 주소로 물어 벡터 검색을 켠다 — 비어 있으면
# 시도조차 안 하고 FTS 로만 답한다(데스크탑의 정상 상태와 같다).
#
# `VINC_EMBED_REMOTE` 가 있으면 **모델이 이 컨테이너에 없다.** 사이드카도 워커도
# 그쪽을 본다. 콜드스타트(0→1 리비전)가 질의 타임아웃(1.5초)보다 길어 첫 질의는
# FTS 로 강등되지만, 그 요청이 리비전을 깨우므로 다음 질의부터 벡터가 붙는다.
# 강등은 응답 메타에 남아 조용하지 않다.
EMBED_PORT="${VINC_EMBED_PORT:-8791}"
if [ "${VINC_EMBED:-on}" = "on" ]; then
  if [ -n "${VINC_EMBED_REMOTE:-}" ]; then
    export VINC_EMBED_ENCODE="${VINC_EMBED_REMOTE%/}"
    echo "[entrypoint] 임베딩 모델은 원격이다 — $VINC_EMBED_ENCODE"
  else
    export VINC_EMBED_ENCODE="http://127.0.0.1:${EMBED_PORT}"
  fi
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
      # 원격 인코더가 있으면 **이 워커는 창구를 열지 않는다**(--encode-port 0).
      # 열어 봐야 모델이 없어 500 만 돌려주고, 사이드카는 이미 원격을 본다.
      if [ -n "${VINC_EMBED_REMOTE:-}" ]; then
        w_port=0; w_where="remote:${VINC_EMBED_REMOTE}"
      else
        w_port="$EMBED_PORT"; w_where="127.0.0.1:$EMBED_PORT"
      fi
      VINC_SIDECAR="http://127.0.0.1:8787" \
        python packaging/embed_worker.py --interval "${VINC_EMBED_INTERVAL:-300}" \
          --encode-port "$w_port" &
      cpid=$!
      echo "$cpid" > "$EMBED_PIDFILE"
      echo "[entrypoint] embed worker started (pid=$cpid, interval=${VINC_EMBED_INTERVAL:-300}s," \
           "encode=$w_where, attempt=$((attempt + 1)))"
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

MCP_ARGS="--host 0.0.0.0 --port 8790 --base http://127.0.0.1:8787 --token $VINC_SIDECAR_TOKEN --no-spawn"
if [ -n "$VINC_MCP_TOKEN" ]; then
  MCP_ARGS="$MCP_ARGS --auth-token $VINC_MCP_TOKEN"
fi
if [ -n "$VINC_MCP_ISSUER" ]; then
  MCP_ARGS="$MCP_ARGS --oauth --issuer $VINC_MCP_ISSUER"
fi

# ⚠ **`exec` 를 쓰지 않는다**(2026-08-07 실측 사고). 예전에는 마지막 줄이
# `exec python -m api.cli mcp serve-http` 였는데, `exec` 는 셸 프로세스를 통째로
# 대체하므로 **그 순간 위의 trap 이 사라진다.** 그러면 컨테이너 종료 시 SIGTERM 이
# PID 1(=MCP)에게만 가고 사이드카는 아무 신호도 못 받은 채 컨테이너와 함께
# SIGKILL 로 죽는다.
#
# 그 결과가 조용한 데이터 유실이다: 사이드카가 `app.close()` 를 못 돌려 **WAL 이
# 체크포인트되지 않고**, 다음 기동에서 `VINC_WAL_REPLAY_TOLERANT=1` 이 그 미완결
# 꼬리를 버린다(관용 옵션은 복구가 아니라 폐기다). 실측: 임베딩 백로그를 109→0 으로
# 다 비운 직후 배포 한 번에 **문서 66건의 needs_embed 해제가 사라졌다.** 임베딩만의
# 문제가 아니다 — 체크포인트 전의 모든 쓰기가 대상이고, 반복된 강제 종료는 오늘 두 번
# 겪은 FTS 인덱스 손상과도 같은 자리다.
#
# 사이드카 쪽은 이미 옳다(`api/server.py` `serve()`: SIGTERM → stop → finally
# `app.close()`). 신호를 전달할 사람이 없었을 뿐이라, 고칠 곳은 여기 하나다.
# shellcheck disable=SC2086
python -m api.cli mcp serve-http $MCP_ARGS &
MCP_PID=$!

# 순서가 의미다.
#   ① 워치독 먼저 — 안 그러면 종료 중인 사이드카를 "죽었다"고 보고 되살린다
#   ② MCP — 새 요청을 끊는다. 사이드카를 먼저 닫으면 처리 중인 요청이 닫힌 스토어를 만난다
#   ③ 사이드카 — **닫을 때까지 기다린다.** 이 기다림이 이 함수의 존재 이유다
# Container Apps 의 종료 유예는 기본 30초라 상한 20초는 그 안에 든다.
shutdown() {
  trap '' TERM INT                      # 두 번째 신호가 이 절차를 자르지 않게
  kill "$WATCHDOG_PID" 2>/dev/null || true
  kill "$EMBED_SUP_PID" 2>/dev/null || true
  kill "$(cat "$EMBED_PIDFILE" 2>/dev/null)" 2>/dev/null || true
  kill "$MCP_PID" 2>/dev/null || true
  sc=$(cat "$SIDECAR_PIDFILE" 2>/dev/null)
  if [ -n "$sc" ]; then
    kill "$sc" 2>/dev/null || true
    i=0
    while [ "$i" -lt 20 ] && kill -0 "$sc" 2>/dev/null; do
      i=$((i + 1))
      sleep 1
    done
    if kill -0 "$sc" 2>/dev/null; then
      # 여기서 침묵하면 다음 기동의 유실 원인을 또 못 찾는다.
      echo "[entrypoint] ⚠ 사이드카가 20초 안에 닫히지 않았다 — WAL 이 미완결로" \
           "남을 수 있다(다음 기동에서 최근 쓰기 유실)" >&2
    else
      echo "[entrypoint] 사이드카 정상 종료 — 스토어 close 완료(${i}s)"
    fi
  fi
  exit 0
}
trap shutdown TERM INT

# `wait` 는 신호가 오면 트랩을 돌리고 돌아온다. MCP 가 스스로 죽은 경우(신호 없이)는
# 그 종료코드를 그대로 물려준다 — 컨테이너 재시작 판단의 근거가 된다.
wait "$MCP_PID"
rc=$?
echo "[entrypoint] MCP 종료(rc=$rc) — 사이드카 정리" >&2
shutdown_rc=$rc
kill "$WATCHDOG_PID" 2>/dev/null || true
kill "$(cat "$SIDECAR_PIDFILE" 2>/dev/null)" 2>/dev/null || true
exit "$shutdown_rc"
