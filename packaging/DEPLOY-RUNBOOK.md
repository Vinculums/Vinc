# 허브 배포 — 통제된 절차

`main` push 는 `hub-deploy` 를 자동으로 돌린다. 그런데 **리비전 교체 순간이
2026-08-04 · 08-07 · 08-09 세 번의 데이터 손상 계기였다.** 이 문서는 그 순간에
잃을 수 있는 양을 **분 단위로 자르는** 절차다.

⚠ 이 절차는 손상을 **예방하지 않는다.** 손상은 저장 계층 문제이고 처방은 따로다
(그래프 `vinc.ops.fts-index-drift` · 리포 루트 `INCIDENT-DOSSIER-storage.md`).
여기서 하는 것은 **손실 상한을 정하는 것**이다.

---

## 왜 필요한가

손상되면 **고칠 수 없다.** 드라이버 관용 옵션 다섯 조합을 전부 시도해도 열기 시점에서
실패한다(실측). 유일한 복구는 **옛 사본으로 되돌아가는 것**이고, 그래서 배포 직전
백업이 없으면 손실이 **날 단위**가 된다. 실제로 `db.recovered3/4/5` 가 전부 같은
`db.recovered2`(2026-08-08 06:38 UTC) 사본이라 매번 같은 과거에 착지했다.

---

## 절차

### 0. 사전 상태를 숫자로 고정

```
vinc_domains          → 도메인별 노드 수(합계를 적어 둘 것)
vinc_doc_list limit=1 → total_count
```

배포 후 이 숫자와 대조한다. "떴다" 는 검증이 아니다 — **빈 DB 도 200 을 준다**
(2026-08-04 실사고).

### 1. 리비전을 내린다 — `update` 를 쓰지 말 것

⚠ **`az containerapp update --min-replicas 0` 은 새 리비전을 만들어 띄운다.**
그게 구 리비전과 락을 다투므로, 겹침을 없애려는 목적에 정반대다(2026-08-09 실측).

리비전을 만들지 않는 경로는 이것뿐이다:

```bash
az containerapp revision deactivate -n vinc-hub -g vincsRG --revision <현재 리비전>
```

활성 리비전이 0이 될 때까지 기다린다:

```bash
az containerapp revision list -n vinc-hub -g vincsRG --query "[?properties.active].name" -o tsv
```

**로그에서 두 줄을 확인한다** — 이게 없으면 다음 단계로 가지 않는다:

```
vinc: final snapshot — ok                    ← 순서상 먼저
[entrypoint] 사이드카 정상 종료 — 스토어 close 완료(Ns)
```

`VINC_STORE_LOCAL=on` 에서 작업본은 **컨테이너 임시 디스크**에 있다. 종료 시 강제
스냅샷이 먼저 돌아야 영속 볼륨에 남는다(`App.close`: 큐 드레인 → 강제 스냅샷 →
store close). 첫 줄이 `ok` 가 아니면 **그 시점 이후 쓰기가 사라진다** — 멈출 것.

과거 구간은 라이브 tail 로 안 보인다. Azure MCP 로 조회할 것(§부록).

> 이 줄은 **필요조건이지 충분조건이 아니다.** 2026-08-09 I4b 는 이 줄을 찍고도
> 6초 뒤에 깨졌다. 그래서 2단계가 있다.

### 2. 백업 — 뜨고, **열어 본다**

락이 풀렸으므로 서버측 복사가 된다(잠겨 있으면 `FileLockConflict` 가 난다).

```bash
az storage file copy start --account-name vincstorage01 --account-key "$K" \
  --source-share hub-data --source-path <현재 db> \
  --destination-share hub-data --destination-path db.bak-<YYYYMMDD-HHMM>
```

**받아서 실제로 연다.** 열어보지 않은 백업은 백업이 아니라 추측이다 —
`db.recovered3.bad-20260809` 는 복사 성공에 크기도 맞았지만 열면 죽는다.

```bash
az storage file download --share-name hub-data --path db.bak-<...> --dest ./bak/db \
  --account-name vincstorage01 --account-key "$K" --no-progress
```

⚠ **받은 크기를 반드시 확인할 것.** 복사 직후에는 `copyStatus: success` 인데도
다운로드가 **0바이트**로 오는 경합이 있다(2026-08-09 에 두 번). 0이면 다시 받는다.

docker 에서 연다(Windows 휠에는 FTS 확장이 없어 로컬에서 못 연다):

```bash
docker run --rm -v "$PWD:/w" -w /w python:3.12-slim sh -c '
  mkdir -p /root/.lbdb && cp -r /w/ext/extension /root/.lbdb/
  pip install -q ladybug==0.21.2
  python - <<PY
import ladybug
c = ladybug.Connection(ladybug.Database("/w/bak/db"))
for lb in ("Concept","Record","Episode","Bridge","Domain","Chunk","_DocMeta"):
    print(lb, c.execute(f"MATCH (x:{lb}) RETURN count(x)").get_next()[0])
PY'
```

0단계 숫자와 맞지 않으면 **배포하지 않는다.**

### 2-1. 엔진 판을 올리는 배포라면 — 공유 스냅샷을 뜬다(ladybug 0.18.0 → 0.20.4)

⚠ **저장 형식은 한 방향이다.** 0.20.4 는 저장 형식 47 을 쓰고 40~47 을 읽는다. 0.18.0
(형식 42)이 쓴 파일은 0.20.4 가 열지만, **0.20.4 가 한 번이라도 쓴 파일은 0.18.0 이 못
연다**(열기에서 `store open failed` → `/health` 의 `open_error`). 즉 이 배포의 롤백은 이미지를
되돌리는 것이 아니라 **배포 직전 사본으로 되돌아가는 것**이다. 근거:
그래프 `vinc.ops.ladybug-upgrade-review-2026-09-24` §2.6.

1단계(리비전 내리기)와 2단계(백업) 뒤, 3단계 전에 **Azure Files 공유 스냅샷**을 뜬다.
공유 스냅샷은 파일 락과 무관하고 공유 전체를 한 시점으로 고정한다:

```bash
az storage share snapshot --name hub-data --account-name vincstorage01 --account-key "$K"
```

출력의 `snapshot` 타임스탬프를 적어 둔다. 스냅샷이 떠진 것을 확인하기 전에는 push 하지 않는다:

```bash
az storage share list --account-name vincstorage01 --account-key "$K" \
  --include-snapshots --query "[?name=='hub-data'].snapshot" -o tsv
```

⚠ 이 한 번은 2단계의 "열어 본다"를 **0.18.0 으로** 한다(`pip install ladybug==0.18.0`) —
되돌아갈 곳이 0.18.0 이 여는 사본이어야 하기 때문이다. 0.20.4 로 열면 받은 로컬 사본이
형식 47 로 올라가 그 검증이 무의미해진다(공유의 원본은 무사하다).

**롤백** — 이미지만 되돌리면 0.18.0 이 새 파일을 못 열어 빈 그래프로 뜬다. 순서:

1. 리비전을 내린다(1단계와 같다).
2. 스냅샷에서 배포 직전 파일을 새 이름으로 꺼낸다:
   ```bash
   az storage file download --share-name hub-data --path <현재 db> --snapshot <ts> \
     --dest ./pre-0204/db --account-name vincstorage01 --account-key "$K" --no-progress
   az storage file upload --share-name hub-data --source ./pre-0204/db \
     --path db.pre-0204 --account-name vincstorage01 --account-key "$K"
   ```
   (받은 크기가 0이면 다시 받는다 — 2단계의 경합과 같다.)
3. 0.18.0 을 핀한 이전 이미지로 되돌리고 5단계의 한 줄로 `VINC_DATA_DIR=/data/db.pre-0204`
   를 넣는다(**PowerShell** 로).

배포 후 쓰인 것은 이 롤백으로 사라진다 — 그래서 4단계 검증을 배포 직후에 끝낸다.

### 3. 배포

활성 리비전이 0인 상태에서 push 한다 — 락을 다툴 상대가 없다.

```bash
git push origin main      # hub-deploy: gate(테스트) → acr build → update --image
```

`hub-deploy` 의 "구 리비전 비활성화" 루프는 활성 리비전이 없으므로 no-op 이다.

### 4. 검증 — 숫자로

```
/health              → status ok · sidecar.reachable · docs 수
                       **graph_snapshot** → state·age_s (아래)
vinc_domains         → 0단계 합계와 일치하는가
vinc_doc_list        → total_count 일치하는가
```

`graph_snapshot` 이 "백업이 실제로 도는가"의 유일한 외부 신호다:

| 값 | 뜻 |
|---|---|
| `state: "ok"` · `age_s` 가 작다 | 정상 |
| `age_s: null` | **이 프로세스가 사는 동안 한 번도 성공한 적 없다.** 가장 위험 |
| `state: "error: …"` | 스냅샷이 실패하고 있다 — 작업본은 임시 디스크다 |
| `state: "off"` | `VINC_STORE_SNAPSHOT` 미설정 = 전환이 안 켜져 있다 |
| `pending_write: true` 가 오래 | 쓰기는 쌓이는데 안 뜨고 있다 |

⚠ **스냅샷 여부를 파일 mtime 으로 재지 말 것.** Azure REST API 의 `lastModified` 와
컨테이너가 CIFS 로 보는 mtime 이 다르다(2026-08-09 실측: 05:46 vs 05:50). 그 차이로
"스냅샷이 안 뜬다"고 20분 오판했다. `graph_snapshot` 을 보거나, 확실히 하려면
스냅샷을 받아 **내용**으로 확인할 것.

컨테이너 로그에 이것이 **없어야** 한다:

```
store open failed / driver: null / store restore — error:
```

### 4-1. `min-replicas` 를 되돌릴 때 (배수했다면)

⚠ **`az containerapp update --min-replicas N` 은 비활성화해 둔 리비전을 되살린다.**
2026-08-09 실측: 58을 deactivate 한 뒤 이 명령을 쓰자 58이 `Activating` 으로
돌아오고 새 리비전 59와 락을 다퉜다. 배수해 놓은 상태가 무효가 된다.

그래서 순서가 이렇다:

```bash
az containerapp update -n vinc-hub -g vincsRG --min-replicas 1   # 새 리비전 N+1 생성
az containerapp revision deactivate -n vinc-hub -g vincsRG --revision <되살아난 구 리비전>
az containerapp revision restart -n vinc-hub -g vincsRG --revision <N+1>
```

마지막 줄이 필요한 이유: **사이드카는 락을 기동 시 한 번만 시도하고 재시도하지
않는다.** 구 리비전이 사라져 락이 풀려도 이미 대기에 들어간 리비전은 300초를
채우고 `exit 1` → 컨테이너 재시작을 거쳐야 열린다. 락이 풀린 것을 확인했으면
`revision restart` 로 그 5분을 건너뛴다.

락이 실제로 풀렸는지 확인:

```bash
az containerapp replica list -n vinc-hub -g vincsRG --revision <구 리비전>   # 비어야 한다
az storage share list-handle --name hub-data --account-name vincstorage01 \
  --account-key "$K" --recursive -o table                                    # 0건이어야 한다
```

### 5. 실패하면 — 롤백 한 줄

```bash
az containerapp update -n vinc-hub -g vincsRG \
  --set-env-vars "VINC_DATA_DIR=/data/db.bak-<YYYYMMDD-HHMM>"
```

⚠ **env 는 PowerShell 로 넣을 것.** Git Bash 는 MSYS 경로 변환이 값을 바꿔치기한다
(`/data/db` → `D:\git\data\db`, 2026-08-04 실사고 — 그 결과 빈 그래프가 200 을 주며
"정상"으로 보였다).

---

## 부록 — 과거 로그 조회

`az containerapp logs show` 는 **라이브 tail 뿐**이다. 지나간 구간은 Log Analytics 다.
그리고 `az monitor log-analytics query` 는 **KQL 을 인자 파서에 태워 망가뜨린다**
(리비전명의 `--` 가 인자 구분자로 잘리고 `endswith` 는 SEM 에러). Azure MCP 를 쓸 것 —
쿼리를 JSON 필드로 그대로 넘긴다.

```
monitor_workspace_log_query
  tenant         74b27512-a326-4c42-8ca9-41214d8ba4f9   ← 빠뜨리면 조용히 빈 배열
  subscription   vincs
  resource-group vincsRG
  workspace      58fdbd61-e38f-47e0-9479-5edd8f73d756
  table          ContainerAppConsoleLogs_CL
  query          ContainerAppConsoleLogs_CL | where … (표 이름을 다시 쓴다)
  hours          <넉넉히>   ← 범위 밖이면 status 200 에 빈 배열이 온다
```

빈 결과가 오면 `query: "recent"` 로 도구 생사부터 가를 것.
