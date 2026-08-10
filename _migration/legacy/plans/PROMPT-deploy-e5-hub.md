# 배포 프롬프트 — 허브 e5 전환 (스키마 v8 · 부모-자식 벡터 · 전량 재임베딩)

> 아래 `---` 사이 전체를 복사해 새 세션에 붙여 넣는다. Azure CLI 또는 Azure MCP 가 있는
> 환경이어야 한다. 이 배포는 **되돌릴 수 있다**(롤백 절차 포함).

---

## 임무

Vinc 허브(`vinc-hub`, Azure Container Apps)를 배포해 임베딩 모델을 영어 전용
`bge-base-en-v1.5` 에서 다국어 `multilingual-e5-base` 로 전환한다. 코드 작업은 끝났고
(`Vinc` 리포 `main` 기준 커밋 `be46b05` 까지) **남은 것은 배포와 그 뒤 자동으로 도는
재임베딩의 관찰**뿐이다.

작업 루트: `D:/Projects/AIProjects/vinc-project` (하위 `Vinc`, `Vinc-docs`, `Vinc-homepage`)

## 왜 하는가 (한 줄씩)

- 발주자가 **영어와 한국어를 함께** 쓰기로 정했다. 현행 bge 는 vocab 에 한글 음절
  토큰이 **0개**라(자모로 분해) 한국어 검색이 무너져 있었다.
- 실측(한영 60질의): 한국어 nDCG **0.347 → 0.670**(거의 두 배), 영어는 5% 손실.
- 벡터 검색은 **애초에 켜진 적이 없었다**(호출부가 `query_embedding` 을 안 넘겼다).
  이번 배포로 처음 켜진다.

## 배포하면 실제로 일어나는 일

푸시하면 `Vinc/.github/workflows/hub-deploy.yml` 이 자동으로 돈다(트리거 경로에
`api/**`·`store/**`·`Dockerfile.hub`·`packaging/**` 이 있고 이번 변경이 전부 해당).

1. **이미지 빌드가 길어진다.** `Dockerfile.hub` 가 빌드 타임에 e5 fp32(약 1GB)를 받아
   `reduce_range=True` 로 양자화해 279MB + 토크나이저 17MB 를 굽는다. 이미지가
   500MB → 약 780MB 가 된다. ⚠ 런타임 양자화는 **불가능**하다(피크 1.5GB, 컨테이너는
   1024MiB) — 그래서 굽는다. 자세한 근거는 `Dockerfile.hub` 주석.
2. **스키마가 v7 → v8** 로 오른다. `ChunkVec` 노드 테이블이 새로 생긴다(ALTER 없음 —
   매 부팅 멱등 DDL 이 만든다). 기존 테이블·데이터는 건드리지 않는다.
3. **워커 첫 라운드가 모델 전환을 트리거**한다. 워커가 `?model=intfloat/...` 를 알리고,
   사이드카가 기록된 모델(없음)과 달라 **옛 벡터를 전부 지우고 전 문서를 재임베딩
   대기**로 돌린다. 기록이 없으면 출처를 모르므로 지운다 — 지금이 정확히 그 경우다.
4. **그 순간부터 검색은 FTS 로만** 답한다(`arms: "fts"`, `vector_note` 에 진행도).
   사용자 체감 손실은 없다 — 벡터 검색은 원래 안 켜져 있었다.
5. 워커가 5분마다 16문서씩 채운다. 다 끝나면 **저절로** `arms: "fts+vector"` 로 바뀐다.

## 배포 전 반드시 확인할 것 (순서대로)

### A. `VINC_EMBED_MODEL` 이 컨테이너에 **명시돼 있지 않은지**

이게 이번 배포의 최대 함정이다. 모델은 코드 기본값으로 바뀌는데, 컨테이너 앱에
`VINC_EMBED_MODEL=BAAI/bge-base-en-v1.5` 가 손으로 박혀 있으면 **env 가 이겨서 아무것도
바뀌지 않는다**. 그리고 허브 env 는 어디에도 코드화돼 있지 않다(`az containerapp` 으로
손으로 넣은 값이고 `scripts/check_hub_env.py` 도 `VINC_EMBED_*` 는 검사하지 않는다).

```bash
az containerapp show -n vinc-hub -g vincsRG \
  --query "properties.template.containers[0].env[?starts_with(name,'VINC_EMBED')]" -o table
```

- 아무것도 안 나오면 **정상**(코드 기본값이 적용된다).
- `VINC_EMBED_MODEL` 이 bge 로 박혀 있으면 **지우거나** e5 로 바꾼다:
  ```bash
  az containerapp update -n vinc-hub -g vincsRG \
    --set-env-vars VINC_EMBED_MODEL=intfloat/multilingual-e5-base
  ```
- `VINC_EMBED=off` 가 있으면 워커가 아예 안 뜬다 — 있으면 발주자에게 확인할 것.

### B. 기존 필수 env 가 그대로 있는지

```bash
az containerapp show --name vinc-hub -g vincsRG \
  --query "properties.template.containers[0].env" -o json | python Vinc/scripts/check_hub_env.py
```
8개(`VINC_SIDECAR_TOKEN`·`VINC_DATA_DIR`·`VINC_MCP_TOKEN`·`VINC_MCP_ISSUER`·
`VINC_OAUTH_ADMIN_KEY`·`VINC_MCP_ED25519_SEED`·`VINC_ACCOUNT_PUBLIC_KEY`·
`VINC_CONNECT_URL`)가 다 있어야 한다. `VINC_MCP_ISSUER` 하나가 빠져 커넥터가 전면
불가였던 사고 전례가 있다(그리고 `/health` 에는 안 드러난다).

### C. 배포 직전 상태 기록 (롤백·비교용)

```bash
az containerapp revision list -n vinc-hub -g vincsRG \
  --query "[?properties.active].{rev:name,created:properties.createdTime}" -o table
curl -s https://mcp.vincs.io/health
curl -s "https://mcp.vincs.io/.well-known/oauth-protected-resource" -o /dev/null -w "%{http_code}\n"
```
현재 활성 리비전 이름을 적어 둔다 — 롤백은 그 이름으로 한다.

### D. 로컬 테스트가 초록인지

```bash
cd Vinc && env -u PYTHONHOME -u PYTHONPATH -u PYTHONSTARTUP PYTHONIOENCODING=utf-8 \
  uv run --python 3.13 python -m pytest tests -q
```
기대: **651 passed**. 하나라도 빨간색이면 배포하지 말 것.

## 배포

```bash
cd Vinc && git log --oneline -8      # be46b05 가 최신인지 확인
git push origin main                  # ← 이것이 배포다(hub-deploy.yml 자동 실행)
```

⚠ **`Vinc-docs`·`Vinc-homepage` 는 이번 배포와 무관**하다. `Vinc` 만 푸시한다.
⚠ 작업트리에 **다른 세션의 미커밋 작업**이 있을 수 있다(graph-md 계열). `git status` 로
확인하고, 내 것이 아닌 변경은 **건드리지 말 것**.

진행 관찰:
```bash
gh run watch          # 또는 gh run list --workflow=hub-deploy.yml
```
빌드가 평소보다 오래 걸린다(모델 양자화 때문). 그게 정상이다.

## 배포 후 확인 (이 순서로)

### 1. 살아 있나
```bash
curl -s https://mcp.vincs.io/health                                   # {"status": "ok"}
curl -s https://mcp.vincs.io/.well-known/oauth-protected-resource      # 200
curl -sI -X POST https://mcp.vincs.io/mcp | grep -i www-authenticate   # https:// 여야 함
```

### 2. 모델이 실제로 준비됐나 (컨테이너 로그)
```bash
az containerapp logs show -n vinc-hub -g vincsRG --tail 200 --follow
```
기대하는 줄:
- `[entrypoint] embed worker started (... encode=127.0.0.1:8791)`
- `[embed] loopback ok · pending=N` ← 워커가 사이드카에 닿았다는 증거
- `[embed] model ready (intfloat/multilingual-e5-base, onnx, ...)`
- `[embed] doc=... chunks=... cleared=True` ← 재임베딩이 도는 중

⚠ 이런 줄이 보이면 **문제**다:
- `[embed] 양자화 실패(...)` 또는 `양자화 불가(...)` → 구워진 모델을 못 찾았다는 뜻.
  `VINC_EMBED_MODEL_PATH` 가 이미지에 설정됐는지 확인(`Dockerfile.hub` 의 `ENV`).
  이대로 두면 fp32(1,058MB)를 올리려다 OOM 난다.
- 로그가 조용하다 → 워커가 안 떴을 수 있다. `VINC_EMBED=off` 를 의심할 것.

### 3. 재임베딩 진행도 (외부에서 보이는 창)
```bash
curl -s "https://mcp.vincs.io/health"    # 인증 없이 되는 건 여기까지
```
진행도는 **검색 응답**에 실린다. MCP 토큰이 있으면:
```bash
curl -s -H "Authorization: Bearer $VINC_MCP_TOKEN" \
  "https://mcp.vincs.io/..."   # 실제 검색 경로는 MCP-INTERFACE.md 참조
```
- 이행 중: `"arms": "fts"` + `"vector_note": "embedding migration in progress (N docs pending) — FTS only"`
- 완료: `"arms": "fts+vector"`, `vector_note` 없음

`N` 이 줄어들면 정상. **줄지 않으면** 워커가 막힌 것이니 로그를 볼 것.
문서 1,000건 기준 약 5.2시간(5분마다 16문서).

### 4. 완료 후 품질 확인
```bash
cd Vinc && python scripts/eval_search.py --data <스토어 경로> --owner <owner-id>
```
프로덕션 경로(FTS / 벡터 / RRF)를 실제로 비교한다. 사람이 쓴 질의 파일이 있으면
`--queries eval.jsonl` 로 넘기는 편이 낫다(기본은 제목 기반이라 어휘 매칭에 유리한 편향).

## 실패 시 롤백

**되돌릴 수 있다.** 청크(정본)는 어느 방향으로도 안 바뀐다 — 이번 변경은 재청킹이 아니다.

리비전 롤백(가장 빠름):
```bash
az containerapp ingress traffic set -n vinc-hub -g vincsRG \
  --revision-weight <배포전_리비전이름>=100
```

모델만 되돌리기(코드는 그대로 두고):
```bash
az containerapp update -n vinc-hub -g vincsRG \
  --set-env-vars VINC_EMBED_MODEL=BAAI/bge-base-en-v1.5
```
같은 전환 트리거가 반대 방향으로도 작동한다(옛 벡터 지우고 전량 재임베딩).
단 bge 는 구워진 모델이 없으므로 런타임에 fp32(436MB)를 받는다 — 그건 컨테이너에 들어간다.

⚠ **스키마 v8 은 되돌리지 않는다.** `ChunkVec` 테이블이 남을 뿐이고 v7 코드는 그걸
쳐다보지 않으므로 무해하다. 테이블 드롭을 시도하지 말 것(위험하고 이득이 없다).

## 이 배포에서 하지 말아야 할 것

- **DB 파일을 직접 만지지 말 것.** LadybugDB 는 배타 락이라 두 프로세스가 같은 볼륨을
  물면 스토어가 안 열린다. `az containerapp exec` 로 들어가 파이썬으로 스토어를 여는
  것도 같은 이유로 금지.
- **두 리비전을 동시에 활성으로 두지 말 것.** 같은 Azure Files 볼륨을 물어 락이 충돌한다
  (`DEPLOYMENT.md` §4 에 명시). 트래픽은 항상 한 리비전에 100%.
- 재임베딩이 도는 동안 **자원을 줄이지 말 것**(0.5 vCPU / 1Gi 유지).

## 보고할 것

배포가 끝나면 다음을 정리해 보고한다:
1. 빌드·배포 소요 시간과 이미지 크기 변화
2. 위 §2 의 로그 줄이 실제로 나왔는지(그대로 인용)
3. 재임베딩 시작 시점의 `pending` 수와, 30분 뒤 줄어든 수
4. `/health` 와 OAuth 디스커버리 3종의 상태코드
5. 문제가 있었다면 무엇을, 그리고 롤백했는지

**추측으로 보고하지 말 것.** 확인 못 한 것은 "확인 못 함"이라고 적는다. 이 프로젝트는
"에러 없이 조용히 나빠지는" 사고를 반복해 왔고, 이번 배포의 관찰 지점(로그·`arms`·
`vector_note`)은 전부 그걸 드러내려고 만든 것이다.

---

## 부록 — 이번 배포에 담긴 코드 변경

| 커밋 | 내용 |
|---|---|
| `8540e29` | 인덱스 드롭을 벡터로 한정 — 임베딩 중 키워드 검색이 100% 실패하던 것 |
| `9539005` | 벡터 검색 배선(워커 루프백 `/encode`) — 임베딩이 write-only 였다 |
| `bf51cbb` | `scripts/eval_search.py` — FTS/벡터/RRF 비교 도구 |
| `028493c` | 토큰 기준 청킹 기계장치(옵트인, 기본 동작 불변) |
| `916dbca` | 계열별 풀링(bge=CLS, e5=mean) + reduce_range 양자화 |
| `7c0dec0` | **부모-자식 벡터**(스키마 v8, `ChunkVec`) |
| `cc4845e` | 모델 기본값 e5 + 재임베딩 트리거 + 이행 중 벡터 차단 |
| `be46b05` | 양자화를 빌드 타임으로(런타임은 OOM) |

설계 근거 전문: `Vinc-docs/plans/PLAN-embedding-retrieval.md`
