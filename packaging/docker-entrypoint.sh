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

# **영속 볼륨 루트** — 아래에서 DATA_DIR 이 로컬 작업본으로 바뀔 수 있으므로 여기서
# 먼저 잡아 둔다. OAuth 스냅샷 기본값이 이걸 쓴다(안 잡아 두면 상태 DB 스냅샷이
# 로컬을 가리켜 **영속이 아니게 된다** — 리비전마다 커넥터가 끊긴다).
PERSIST_ROOT="${DATA_DIR%/*}"

# ── 그래프 DB: 작업본 로컬 + 영속 볼륨 스냅샷 (2026-08-09) ────────────────────
# OAuth SQLite 에 쓴 것과 **같은 처방**이다(아래 ① ② 참조). 그때 "Azure Files 는
# SQLite 가 기대는 바이트 범위 락을 제대로 주지 않는다 — WAL 만의 문제가 아니다"
# 라고 적어 놓고, 정작 본체인 그래프 DB 는 그대로 SMB 위에 두었다. 그 계층에서
# 하드다운이 세 번 났다(FTS 손상 08-04·08-07, 재생 불가 WAL 08-08).
#
# `VINC_STORE_SNAPSHOT` 이 있으면 사이드카가 기동 시 거기서 복원하고 주기적으로
# 되민다. **비어 있으면 예전처럼 DATA_DIR 에서 직접 돈다** — 전환은 아래 한 줄을
# 켜는 의도적 행위다(배포 시 데이터 이관이 선행되어야 하므로 자동으로 켜지 않는다).
#
#   VINC_STORE_LOCAL=on  →  작업본 /var/lib/vinc/db · 영속 스냅샷 = 원래 DATA_DIR
if [ "${VINC_STORE_LOCAL:-}" = "on" ]; then
  : "${VINC_STORE_SNAPSHOT:=$DATA_DIR}"
  : "${VINC_STORE_WORK_DIR:=/var/lib/vinc}"
  mkdir -p "$VINC_STORE_WORK_DIR" 2>/dev/null \
    || echo "[entrypoint] ⚠ 작업본 경로 생성 실패: $VINC_STORE_WORK_DIR" >&2
  DATA_DIR="$VINC_STORE_WORK_DIR/$(basename "$VINC_STORE_SNAPSHOT")"
  export VINC_DATA_DIR="$DATA_DIR" VINC_STORE_SNAPSHOT
  echo "[entrypoint] 그래프 스토어: work=$DATA_DIR snapshot=$VINC_STORE_SNAPSHOT"
  case "$VINC_STORE_SNAPSHOT" in
    /data/*) : ;;   # 영속 볼륨 안 — 정상
    *) echo "[entrypoint] ⚠ 스냅샷이 영속 볼륨(/data) 밖이다 —" \
            "리비전이 바뀌면 그래프가 통째로 사라진다." >&2 ;;
  esac
fi

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
: "${VINC_MCP_STATE_SNAPSHOT:=$PERSIST_ROOT/mcp/oauth.db}"   # 영속 — 파일 복사만
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
  "$PERSIST_ROOT"/*) : ;;   # 영속 볼륨 안 — 정상
  *) echo "[entrypoint] ⚠ 스냅샷이 영속 볼륨($PERSIST_ROOT) 밖이다 —" \
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
# ⚠ 경로를 env 로 연다. 컨테이너 안에서는 한 벌뿐이라 고정 경로가 맞지만, **한 기계에서
# 이 스크립트를 여러 번 돌리면**(검사가 그렇게 한다) 서로의 pidfile 을 덮어써서 워치독이
# 남의 살아 있는 프로세스를 보고 "사이드카 정상"이라 판단한다. 실측으로 물렸다.
SIDECAR_PIDFILE="${VINC_SIDECAR_PIDFILE:-/tmp/vinc-sidecar.pid}"

start_sidecar() {
  python -m api.cli serve --host 127.0.0.1 --port 8787 \
    --token "$VINC_SIDECAR_TOKEN" --data "$DATA_DIR" &
  echo $! > "$SIDECAR_PIDFILE"
}

# 한 번만 묻는다. 워치독이 매 주기 이걸 쓰므로 대기 루프와 갈라 둔다.
sidecar_healthy() {
  # ⚠ --max-time 없이는 이 호출이 무한 대기할 수 있다: /health 가 DDL 락(예: FTS
  # 재구축, api/docstore.py _with_fts_repair) 뒤에서 응답을 못 하면 curl 이
  # 영원히 블로킹되고, 워치독은 자기 폴링 주기(SIDECAR_POLL_S)와 무관하게 이
  # 한 줄 안에 갇힌다 — 그러면 아래 SIDECAR_HUNG_S 판정 자체가 못 돈다. 타임박스를
  # 둬야 "느림"과 "멈춤"을 구분할 수 있다.
  # 근거: [[record:editing-the-conventions-document-restarts-the-hub-2026-09-09]]
  # `?live=1` 은 스토어를 안 읽는다(상태 판정·스냅샷 상태는 메모리 값). 워치독이 5초마다
  # 부르므로, 매번 리더를 열어 임베딩 백로그를 세면 허브가 하루 종일 활성 요금 구간에
  # 머문다. DDL 이 오래 쥐는 동안에도 "느림"이 "멈춤"으로 오인되지 않는다.
  # 근거: [[vinc.ops.container-apps-cost-rise-2026-09-25]] §4
  curl -sf --max-time "${VINC_HEALTH_CURL_MAX_S:-10}" "http://127.0.0.1:8787/health?live=1" 2>/dev/null \
    | grep -q '"status":[[:space:]]*"ok"'
}

# $1 = 최대 대기(초, 1회/s 폴링). 준비되면 0, 타임아웃이면 1.
wait_for_sidecar_health() {
  i=0
  while [ "$i" -lt "$1" ]; do
    if sidecar_healthy; then
      return 0
    fi
    i=$((i + 1))
    sleep 1
  done
  return 1
}

# ── 벡터 차원 이행 (2026-09-11) ──────────────────────────────────────────────
# 이 이미지의 모델(Dockerfile ENV)이 스토어의 벡터 열과 차원이 다르면, 사이드카를 띄우기
# **전에** 새 스토어로 재구축해 갈아 끼운다. 사이드카 뒤에는 아무도 스토어를 두 번째로
# 못 열고(단일 writer), 기동 대기 300초가 재구축을 먹으면 재시작 루프가 된다.
# 차원이 같으면 아무것도 하지 않는다 — 매 기동 도는 것이 맞다.
# 실패해도 옛 스토어는 그대로다. 그때는 워커를 끈다: 워커가 새 모델을 들고 오면 옛 벡터를
# 전부 지운 뒤 새 폭의 벡터를 옛 열에 쓰다 막히는데, 그 상태보다 "벡터 그대로·의미 팔만
# 죽음"이 낫고 되돌림(이전 이미지 재배포)도 그쪽이 쉽다.
# 근거: [[decision:bge-m3-adoption]]
rc=0
python -m api.cli migrate-dim --data "$DATA_DIR" || rc=$?
if [ "$rc" = "3" ]; then
  echo "[entrypoint] ⚠ 벡터 차원 재구축 실패 — 옛 스토어로 기동한다. 벡터를 지키기 위해" \
       "임베딩 워커를 끈다(VINC_EMBED=off). 이 리비전은 텍스트 팔로만 답한다." >&2
  export VINC_EMBED=off
elif [ "$rc" != "0" ]; then
  echo "[entrypoint] 기동 중단 — migrate-dim rc=$rc" >&2
  exit 1
fi

start_sidecar

# 사이드카가 죽으면 컨테이너 전체를 내린다(둘 중 하나만 죽어 반쪽 상태로
# 떠 있는 것을 방지 — Container Apps 가 재시작을 판단할 신호가 된다). pidfile 로
# 읽으므로 아래 워치독이 재기동시킨 뒤에도 항상 **현재** 사이드카를 겨눈다.
trap 'kill "$(cat "$SIDECAR_PIDFILE" 2>/dev/null)" 2>/dev/null' TERM INT

# 첫 기동 대기는 **길어야 한다**. 30초는 "정상 기동"에는 넉넉하지만 **WAL 재생**에는
# 턱없이 짧다. 2026-08-08 사고: 대량 쓰기로 WAL 이 11.9MB 까지 자란 상태에서 사이드카가
# 죽자, 재기동마다 재생이 30초를 넘겨 `exit 1` → 컨테이너 재시작 → 재생 처음부터 →
# **영영 못 끝나는 루프**가 됐다. 크래시 자체보다 이 루프가 복구를 막았다.
#
# Azure Files 위에서 12MB WAL 재생은 분 단위다. 그래서 기본 300초로 두고 env 로 연다.
# 진행 상황을 30초마다 찍는다 — 아무 말 없이 5분을 기다리면 "멈춘 것"과 "재생 중"이
# 구분되지 않는다(이 프로젝트가 반복해 겪은 무음 고장의 형태다).
SIDECAR_BOOT_TIMEOUT="${VINC_SIDECAR_BOOT_TIMEOUT:-300}"
# 헬스 폴링 1회분의 시도 수(1회/s 이므로 곧 초다). 아래 `boot_waited` 증분과 재기동
# 뒤 대기가 **같은 값을 써야 한다** — 한 자리만 바꾸면 부팅 진행 회계가 조용히
# 어긋나고 `SIDECAR_BOOT_TIMEOUT` 이 딴 시간을 뜻하게 된다.
# 근거: [[vinc.plan.watchdog-test-timers]] §3
SIDECAR_HEALTH_TRIES="${VINC_SIDECAR_HEALTH_TRIES:-30}"
echo "[entrypoint] waiting for sidecar health (최대 ${SIDECAR_BOOT_TIMEOUT}s — WAL 재생 여유)..."
boot_waited=0
while [ "$boot_waited" -lt "$SIDECAR_BOOT_TIMEOUT" ]; do
  if wait_for_sidecar_health "$SIDECAR_HEALTH_TRIES"; then
    echo "[entrypoint] sidecar ready (${boot_waited}s 대기)"
    break
  fi
  boot_waited=$((boot_waited + SIDECAR_HEALTH_TRIES))
  # 프로세스가 살아 있으면 재생 중일 수 있다 — 죽었으면 기다릴 이유가 없다.
  if ! kill -0 "$(cat "$SIDECAR_PIDFILE" 2>/dev/null)" 2>/dev/null; then
    echo "[entrypoint] sidecar 프로세스가 사라졌다 — 대기 중단(${boot_waited}s)" >&2
    exit 1
  fi
  echo "[entrypoint] … 아직 뜨지 않음(${boot_waited}s/${SIDECAR_BOOT_TIMEOUT}s) — 프로세스는 살아 있다"
done
if [ "$boot_waited" -ge "$SIDECAR_BOOT_TIMEOUT" ]; then
  echo "[entrypoint] sidecar health check timed out (${SIDECAR_BOOT_TIMEOUT}s)" >&2
  exit 1
fi

# ── 노드 미러 전량 재구축 (2026-09-10) ────────────────────────────────────────
# 의미 팔(`VINC_NODE_SEMANTIC_ARM`, 기본 켜짐)이 읽는 `NodeVec` 미러를 여기서 한 번
# 다시 만든다. **증분 갱신은 없다** — "없던 벡터가 생기는" HNSW 삽입만 프로세스를
# 죽이므로(exit 139) 미러는 전량 재구축 아니면 아무것도 아니다.
#
# 컨테이너 기동은 곧 **배포 경계**다. 그래서 그 비용(노드 수에 비례, 실측 1365노드에
# 약 6분)이 일상 질의 경로가 아니라 릴리스에 한 번 물린다. 평시 노드 쓰기는 미러
# 지연을 허용한다 — 그동안에도 제목 팔·그래프 팔·FTS 는 그대로 산다.
#
# 배경으로 보낸다. 앞세우면 MCP 가 몇 분 늦게 뜨고, Container Apps 는 그 사이 리비전을
# 실패로 판정한다. 사이드카는 인코딩을 쓰기 큐 **밖에서** 하므로 이게 도는 동안에도
# 쓰기는 짧게만 막힌다(교체 단계뿐).
#
# 끄는 길: `VINC_NODE_MIRROR_REBUILD=off`. 인코더가 없으면 사이드카가 503 으로
# 거절하고 로그에 남는다 — 조용히 넘어가지 않는다.
#
# 🔴 **종료 때 이것부터 걷어낸다.** 도는 중에 컨테이너가 내려가면 이 요청이 닫으려는
# 사이드카를 붙든다 — 그러면 `shutdown()` 의 20초 대기를 넘기고, Container Apps 의
# 유예(30초)도 넘겨 SIGKILL 이 된다. 그 끝은 체크포인트 안 된 WAL 이고 다음 기동이
# 미완결 꼬리를 버린다(2026-08-07 문서 66건 유실의 그 사슬 그대로).
# 그래서 `curl` 의 pid 를 **파일로** 남긴다 — 서브셸만 죽이면 curl 은 고아로 살아남아
# 연결을 그대로 쥔다. 사이드카·워커가 같은 이유로 같은 형태를 쓴다.
MIRROR_PIDFILE="${VINC_NODE_MIRROR_PIDFILE:-/tmp/vinc-node-mirror.pid}"
MIRROR_SUP_PID=""
if [ "${VINC_NODE_MIRROR_REBUILD:-on}" = "on" ]; then
  (
    # ⚠ 세 줄 다 `node-mirror:` 로 시작한다 — **ASCII 표식이 있어야 밖에서 잡는다.**
    # 이 결과를 읽는 유일한 곳은 Azure 자격이 있는 hub-deploy 이고, 거기서 한국어로
    # grep 하면 러너 로케일에 따라 조용히 0건이 된다(이 리포가 인코딩으로 물린 이력이
    # 있다). 표식을 바꾸려면 `.github/workflows/hub-deploy.yml` 의 grep 도 같이 고칠 것.
    echo "[entrypoint] node-mirror: start — 배포마다 한 번, 배경으로 돈다"
    t0=$(date +%s)
    # 허브가 먼저 올라오고 인코더는 뒤에서 같은 SHA 로 갱신된다. 구 인코더로
    # 미러를 만들면 벡터의 판을 잘못 기록하므로 새 인코더의 실제 지문을 확인한 뒤
    # 한 번만 시작한다. /health 는 모델을 적재하지 않는다.
    if [ -n "${VINC_EMBED_REMOTE:-}" ]; then
      mine=$(python3 -c 'from api.encoder_runtime import fingerprint; print(fingerprint())')
      ready=0
      for i in $(seq 1 60); do
        remote=$(curl -fsS --max-time 5 "${VINC_EMBED_REMOTE%/}/health" 2>/dev/null \
          | python3 -c 'import json,sys; print((json.load(sys.stdin) or {}).get("encoder_runtime") or "")' \
            2>/dev/null || true)
        if [ -n "$mine" ] && [ "$remote" = "$mine" ]; then
          ready=1
          break
        fi
        sleep 10
      done
      if [ "$ready" != 1 ]; then
        echo "[entrypoint] node-mirror: FAILED after $(( $(date +%s) - t0 ))s" \
             "— encoder runtime did not match this hub" >&2
        exit 0
      fi
    fi
    # ⚠ **HTTP 상태를 따로 받는다.** `curl -sS` 는 `-f` 가 없으면 500 에도 종료코드
    # 0 이다. 그래서 2026-09-10 에 네 번 연속 실패한 재구축이 전부 `done` 으로 찍혔고,
    # 사유가 본문에 실려 있었는데도 성공으로 읽혔다. `-f` 는 못 쓴다 — 그러면 본문을
    # 버려서 사유가 사라진다. 본문은 파일로, 상태는 `-w` 로 받는다.
    curl -sS -m "${VINC_NODE_MIRROR_MAX_S:-3600}" -X POST \
      -H "Authorization: Bearer $VINC_SIDECAR_TOKEN" \
      -H "Content-Type: application/json" -d '{}' \
      -o "$MIRROR_PIDFILE.out" -w '%{http_code}' \
      http://127.0.0.1:8787/node-mirror/rebuild > "$MIRROR_PIDFILE.code" 2>&1 &
    cpid=$!
    echo "$cpid" > "$MIRROR_PIDFILE"
    # `set -e` 아래서 `wait` 가 비영을 내면 이 서브셸이 그대로 죽는다(임베딩 감시자와
    # 같은 함정). 상태만 받아 온다.
    rc=0
    wait "$cpid" || rc=$?
    # ⚠ **한 줄로 눌러 담는다.** 사이드카 응답은 `indent=2` 라 여러 줄이고, 컨테이너
    # 로그는 줄 단위라 `node-mirror:` 표식이 붙은 첫 줄에는 `{` 한 글자만 들어간다.
    # 2026-09-10 에 그것 때문에 두 배포를 원인 없이 흘려보냈다.
    # ⚠ **소유자 id 는 찍지 않는다.** 응답의 `owners` 는 raw `user:`/`team:` id 목록이고,
    # `id_collisions` 도 id 를 실어 온다. 이 줄은 컨테이너 stdout 이자 hub-deploy 가 Actions
    # 로그에 다시 찍는 줄이라, 그대로 두면 회원 식별자가 GitHub 에 90일 남는다(2026-09-26
    # 갭 분석, 그래프 vinc.research.privacy-compliance-gap-2026-09-26 §2.3, 판정 D12).
    # 목록은 개수로 접고 나머지 키는 그대로 둔다. JSON 이 아니면(예: 게이트웨이 오류 본문)
    # 그 본문에는 id 가 없으므로 한 줄로 눌러 그대로 싣는다.
    out=$(python3 - "$MIRROR_PIDFILE.out" <<'PYEOF' 2>/dev/null || echo "(unreadable response)"
import json, sys
path = sys.argv[1]
try:
    raw = open(path, encoding="utf-8", errors="replace").read()
except OSError:
    print("(no response body)"); sys.exit(0)
try:
    d = json.loads(raw)
except ValueError:
    print(" ".join(raw.split())); sys.exit(0)
if isinstance(d, dict):
    for k in ("owners", "id_collisions"):
        if isinstance(d.get(k), list):
            d[k] = len(d[k])
print(json.dumps(d, ensure_ascii=False, separators=(",", ":")))
PYEOF
)
    code=$(tr -dc '0-9' < "$MIRROR_PIDFILE.code" 2>/dev/null)
    if [ "$rc" = "0" ] && [ "$code" = "200" ]; then
      echo "[entrypoint] node-mirror: done in $(( $(date +%s) - t0 ))s — $out"
    else
      echo "[entrypoint] node-mirror: FAILED after $(( $(date +%s) - t0 ))s" \
           "(rc=$rc http=${code:-?}) — $out" >&2
    fi
  ) &
  MIRROR_SUP_PID=$!
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
EMBED_PIDFILE="${VINC_EMBED_PIDFILE:-/tmp/vinc-embed.pid}"   # 사이드카 쪽과 같은 이유
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
        "$(cat "$MIRROR_PIDFILE" 2>/dev/null)" "$EMBED_SUP_PID" 2>/dev/null' TERM INT
fi

# ── 사이드카 워치독 (2026-08-03 사고 대응) ───────────────────────────────────
# 그 사고 때 사이드카가 몇 분간 응답 불가였는데(원인은 로그에 남지 않음 — 이번에
# 예외 로깅을 추가했으니 다음엔 남는다) 아무도 감시·재기동하지 않았다: 컨테이너의
# PID 1(아래 exec 로 넘어가는 MCP HTTP)은 계속 살아 `/health` 도 200 을 줬으므로
# Container Apps 관점에서는 "정상"이었고, 사이드카가 스스로 돌아올 때까지(혹은
# 다음 배포까지) 그래프 read/write 전부가 조용히 막혀 있었다.
#
# `SIDECAR_POLL_S`(기본 5초)마다 생사만 확인(kill -0, 가볍다) — 죽었으면 재기동 후
# 헬스를 다시 기다린다.
#
# ⚠ **여기 두 가지가 틀려 있었고 2026-08-28 에 둘 다 물렸다**
# ([[vinc.ops.incident-2026-08-28-hub-sidecar]] §3·§4).
#
# ① 포기 분기가 도달 불가였다. 루프 머리에서 pid 가 살아 있으면 카운터를 0 으로
#    되돌렸는데, **떴다가 죽는 프로세스는 그 순간 살아 있다.** 그래서 카운터는 매 회
#    0 으로 돌아갔고 영원히 1 에서 멈췄다 — 그날 재기동 14회가 전부 `1/5` 로 찍혔다.
#    살아 있음은 성공이 아니다. **일정 시간 건강했을 때만** 회복으로 친다.
# ② 포기해도 소용이 없었다. 작업본이 로컬에 있고 복원은 `restore_if_missing` 이라
#    (있으면 복원하지 않는다), 같은 컨테이너 안에서는 **망가진 작업본을 몇 번을 다시
#    열어도 같은 결과다.** 실제 복구는 새 레플리카뿐이었다. 그러니 포기의 올바른
#    형태는 "워치독만 멈춤"이 아니라 **컨테이너를 내려 플랫폼이 다시 세우게 하는 것**
#    이다. 그래야 빈 파일시스템이 스냅샷에서 복원한다.
#
# 무한 재기동을 피하는 원래 취지(Azure Files 락 회수)는 그대로다 — 상한에 닿으면
# 재기동을 멈추는 것도 같다. 달라진 것은 멈춘 뒤에 **죽는다**는 것뿐이다.
# ③ **죽는 것만 보면 멈추는 것을 못 잡는다.** 하필 이 워치독이 쓰인 계기가 그 모양이다
#    — 2026-08-03 에 사이드카는 살아 있었고 몇 분간 응답만 안 했다. pid 만 보는 감시는
#    그 사고를 다시 만나도 아무 말도 안 한다. 그래서 매 주기 헬스도 함께 묻는다.
#
#    멈춘 사이드카를 **죽여서 되살리지는 않는다.** 그것은 단일 writer 락을 쥔 채 쓰기
#    중일 수 있는 프로세스를 강제 종료하는 새로운 의미이고, 이 리포가 그 자리에서 이미
#    여러 번 다쳤다. 대신 재기동 상한과 **같은 escalation** 으로 보낸다: 컨테이너를
#    내리면 `shutdown()` 이 TERM 을 보내고 최대 20초 닫히기를 기다린 뒤 플랫폼이 새
#    레플리카를 세운다. 이미 있는 정상 종료 경로라 새 위험이 없다.
SIDECAR_STABLE_S="${VINC_SIDECAR_STABLE_S:-300}"   # 이만큼 건강하면 회복으로 친다
SIDECAR_MAX_RESTARTS="${VINC_SIDECAR_MAX_RESTARTS:-5}"
SIDECAR_HUNG_S="${VINC_SIDECAR_HUNG_S:-120}"       # 살아 있는데 이만큼 무응답이면 멈춘 것
SIDECAR_POLL_S="${VINC_SIDECAR_POLL_S:-5}"         # 워치독 루프 주기
SIDECAR_KILL_PAUSE_S="${VINC_SIDECAR_KILL_PAUSE_S:-2}"   # 죽은 뒤 재기동까지의 유예
MAIN_PID=$$                                        # 서브셸에서 이 스크립트를 겨눈다

# 상한에 닿았을 때 하는 일 하나 — 죽는 갈래와 멈추는 갈래가 같은 곳으로 온다.
give_up_and_recycle() {
  echo "[entrypoint] ⚠ $1 — 컨테이너를 내린다. 이 안에서 다시 여는 것으로는 못 고치는" \
       "상태이고(작업본이 로컬이라 복원이 안 걸린다), 새 레플리카만 스냅샷에서 복원한다." >&2
  kill -TERM "$MAIN_PID" 2>/dev/null || true
}
(
  restart_count=0
  restart_at=0            # 마지막 재기동 시각(0 = 아직 없음)
  # 워치독은 기동 헬스가 통과한 **뒤에** 뜬다 — 그러니 지금은 건강한 것이 사실이다.
  # 0 으로 두면 첫 폴링이 무응답일 때 기준 시각이 없어 멈춤을 영영 못 센다.
  last_ok="$(date +%s)"
  while true; do
    sleep "$SIDECAR_POLL_S"
    now="$(date +%s)"
    pid="$(cat "$SIDECAR_PIDFILE" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      if sidecar_healthy; then
        last_ok="$now"
        # 살아 있다고 회복이 아니다(①). 재기동 후 충분히 오래 **건강**했을 때만 되돌린다.
        if [ "$restart_count" -gt 0 ] && [ "$restart_at" -gt 0 ] \
           && [ "$(( now - restart_at ))" -ge "$SIDECAR_STABLE_S" ]; then
          echo "[entrypoint] sidecar 안정(${SIDECAR_STABLE_S}s) — 재기동 카운터 초기화"
          restart_count=0
          restart_at=0
        fi
      elif [ "$(( now - last_ok ))" -ge "$SIDECAR_HUNG_S" ]; then
        give_up_and_recycle "sidecar 가 살아 있는데 ${SIDECAR_HUNG_S}s 무응답이다(멈춤)"
        exit 1
      fi
      continue
    fi
    restart_count=$((restart_count + 1))
    if [ "$restart_count" -gt "$SIDECAR_MAX_RESTARTS" ]; then
      give_up_and_recycle "sidecar 재기동 ${SIDECAR_MAX_RESTARTS}회 연속 실패"
      exit 1
    fi
    echo "[entrypoint] ⚠ sidecar(pid=${pid:-?}) 무응답 —" \
         "재기동 시도 $restart_count/$SIDECAR_MAX_RESTARTS" >&2
    # OS 가 죽은 프로세스의 파일 핸들·락을 정리할 시간(Azure Files=SMB 는 로컬보다
    # 늦게 반영될 수 있다 — 즉시 재기동하면 새 프로세스가 락을 또 못 잡는다).
    sleep "$SIDECAR_KILL_PAUSE_S"
    start_sidecar
    restart_at="$(date +%s)"
    if wait_for_sidecar_health "$SIDECAR_HEALTH_TRIES"; then
      # 여기서 카운터를 0 으로 되돌리지 **않는다** — 헬스가 한 번 통과한 것은 회복이
      # 아니라 회복일 수도 있는 시작점이다(①). 시각만 찍어 두고, 위 루프가 그 뒤로
      # `SIDECAR_STABLE_S` 동안 계속 건강한 것을 보면 그때 회복으로 친다.
      last_ok="$(date +%s)"
      echo "[entrypoint] sidecar 재기동 후 헬스 통과(pid=$(cat "$SIDECAR_PIDFILE" 2>/dev/null))" \
           "— ${SIDECAR_STABLE_S}s 유지되면 회복으로 친다"
    else
      # 헬스는 못 붙었지만 프로세스는 살아 있을 수 있다. `last_ok` 를 지금으로 밀지
      # 않는다 — 그래야 위 루프의 멈춤 판정이 이 무응답 구간을 계속 센다.
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
  # 미러 재구축이 **가장 먼저** 나간다. 이것이 사이드카에 건 요청을 놓지 않으면 아래
  # 20초 대기가 통째로 헛돌고 SIGKILL 로 끝난다(그 자리 주석 참조). curl 을 먼저
  # 죽여야 연결이 실제로 풀린다 — 서브셸만 죽이면 curl 이 고아로 남는다.
  kill "$(cat "$MIRROR_PIDFILE" 2>/dev/null)" 2>/dev/null || true
  kill "$MIRROR_SUP_PID" 2>/dev/null || true
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
