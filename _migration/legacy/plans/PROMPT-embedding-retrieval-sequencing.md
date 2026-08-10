# 분석 의뢰 프롬프트 — 토큰 청킹 → 벡터 검색 활성화 → 다국어 전환

> 이 파일은 **그대로 복사해 쓰는 프롬프트**다. 아래 `---` 사이 전체를 새 세션에 붙여 넣는다.
> 작성 근거는 2026-07-31 실측이며, 인용된 수치는 전부 재현 절차와 함께 본문에 있다.

---

## 역할

너는 Vinc 프로젝트의 검색·임베딩 파이프라인을 설계 검토하는 엔지니어다. 아래 세 단계의
**순서와 구성**이 옳은지 판정하고, 옳다면 각 단계의 실행 설계를, 옳지 않다면 대안을 낸다.

1. **토큰 기준 청킹** 도입 (모든 언어에 이득, 모델 무관)
2. **벡터 검색 실제 활성화** (지금 임베딩은 write-only 다)
3. 그 뒤 품질을 측정하고 **다국어 모델 전환** 판단

작업 루트: `D:/Projects/AIProjects/vinc-project` (하위 `Vinc`, `Vinc-docs`, `Vinc-homepage`, `Vinc-design`)

## 반드시 지킬 규율

- **측정하고 말하라.** 추측을 사실처럼 쓰지 마라. 각 주장에 `실측` / `코드근거(file:line)` /
  `출처확인(URL)` / `추정` 중 하나를 붙인다. `추정`에는 "무엇을 재면 확정되는가"를 함께 적는다.
- **근거 없는 안심 금지.** "아마 괜찮다"는 결론이 아니다. 모르면 모른다고 하라.
- **발주자 대신 결정하지 마라.** 비용·위험이 갈리는 지점은 선택지와 트레이드오프를 제시하고
  질문 형태로 분리한다. 기술적으로 자명한 것만 네가 정한다.
- 이 단계는 **분석 전용**이다. 코드를 고치지 마라. 측정용 스크립트는 스크래치패드에만 쓴다.
- 로컬 파이썬:
  `cd Vinc && env -u PYTHONHOME -u PYTHONPATH -u PYTHONSTARTUP PYTHONIOENCODING=utf-8 uv run --python 3.13 python ...`
  (임시 의존은 `--with tokenizers` 처럼 붙인다. **440MB 모델 다운로드는 하지 마라** — 필요하면
  근거를 적고 발주자 승인을 요청하라.)
- `.venv/`, `node_modules/` 는 검색에서 제외.

## 확정 사실 (재검증 불요 · 다만 인용 전 file:line 은 확인할 것)

### 구조

- **데스크탑**: PyInstaller 사이드카(`packaging/sidecar.spec`). 임베딩 스택(torch·
  sentence-transformers)을 **번들에서 제외**한다(2.7GB 회피). 오프라인 FTS 만 동작.
- **허브**: Azure Container Apps. `Dockerfile.hub` 하나에 **사이드카 + MCP + 임베딩 워커가
  같이** 산다(`packaging/docker-entrypoint.sh`). 자원은 **1024MiB / 0.5 vCPU** 를 셋이 나눠 쓴다.
  실측 정상 사용량은 `WorkingSetBytes` 115MiB.
- 허브 이미지가 설치하는 것: `ladybug==0.18.0 argon2-cffi pypdf` + `onnxruntime tokenizers
  numpy huggingface_hub`. **torch·sentence-transformers·chonkie 는 없다.**

### 임베딩 현황

- 모델 `BAAI/bge-base-en-v1.5`, **공식 fp32 ONNX**(`onnx/model.onnx`, 436MB). 양자화 아님.
  파이프라인: tokenize(truncate 512) → ONNX → `last_hidden_state[:,0]`(CLS) → L2 정규화.
  (`packaging/embed_worker.py`)
- 워커는 **별도 프로세스**다. 사이드카 루프백 `GET /embed/pending` → `POST /embed/apply` 로만
  오간다. 서버 요청 경로에서 이 임베더를 부를 방법이 **없다**.
- 2026-07-31 배치 상한 추가됨: `EMBED_BATCH=8`(`VINC_EMBED_BATCH`). 그 전에는 한 문서의 전
  청크가 단일 세션 실행이라 큰 문서 하나가 컨테이너를 OOM 시킬 수 있었다.

### 🔴 벡터 검색은 켜진 적이 없다

- `q.search()` 의 `query_embedding` 은 기본 `None` 이고, **프로덕션 호출부 두 곳이 모두 넘기지
  않는다**: `api/server.py:270`, `api/cli.py:231`. 따라서 `store.vector_search` 는 호출되지
  않는다. 임베딩은 **write-only** 다.
- 사이드카 안의 유일한 임베더는 `/doc/reembed`(`api/server.py:540-566`)인데
  `engines.embed_pipeline.build_embeddings_model("local-en")` → torch·chonkie 를 요구한다.
  **데스크탑에도 허브에도 그 패키지가 없다.** 즉 두 표면 모두에서 이 경로는 강등된다.
- ⇒ ②는 "`query_embedding` 을 배선한다"가 아니라 **"어느 표면에도 인프로세스 임베더가 없다"**
  는 문제다. 이 점을 축으로 분석하라.

### 청킹과 언어별 토큰 밀도 (실측)

- 청킹은 **문자** 기준: `api/docstore.py` `DEFAULT_MAX_CHARS = 2000`, `chunk_doc`/`plan_boundaries`.
  모델 한계는 **토큰** 기준 512. 이 불일치가 문제의 뿌리다.
- 토크나이저 어휘 30,522 중 **한글 음절 토큰 0개**(자모 70개뿐). `BertNormalizer`
  (`lowercase=true` → `strip_accents` 가 그 값을 따라 true)가 NFD 로 음절을 자모로 분해한다.

| 언어 | 문자/토큰 | 2000자 → 토큰 | 512토큰에 담기는 문자 | **전체 토큰 중 버려지는 양** |
|---|---:|---:|---:|---:|
| 영어 | 4.3 (실제 코퍼스) | 465 | ~2,200 | **5.7%** |
| 한국어 | 0.69 | 2,908 | 352 | **82%** |
| 일본어 | 0.99 | 2,010 | 509 | 75% |
| 중국어 | 0.99 | 2,013 | 508 | 75% |

- 실제 코퍼스(`Vinc/starter-packs/**/*.md`, 125문서 1,149청크, 대부분 영어) 실측 — **truncation 을
  끄고** 잰 진짜 길이다:
  - 토큰 **중앙 469 · p90 605 · p99 819 · 최대 1,400**
  - 512 초과 **362개(31.5%)**, 그 청크들이 잃는 양은 **중앙 9% · 최대 63%**
  - **전체 토큰 중 버려지는 비율 5.7%**
  - 문서당 청크 수는 최대 23 · 중앙 10
- ⚠ **두 수치를 혼동하지 마라.** "31.5%" 는 *영향받는 청크의 비율*이고 "5.7%" 가 *실제 데이터
  손실*이다. 초기 분석에서 이 둘이 뒤섞여 영어 손실이 과장됐다(truncation 을 켠 채 재면 p90·최대가
  둘 다 512 로 나와 상한을 데이터로 착각하게 된다 — 부록 스니펫은 끄고 잰다).
  → 영어에서도 데이터를 버리고 있는 건 사실이나 **5.7% 다.** 토큰 청킹의 진짜 근거는 한국어·
  일본어·중국어의 75~82% 이고, 영어는 부수 이득으로 취급하라. 이 차이가 ①의 시급성 판단을 바꾼다.

### 다국어 후보 (HuggingFace API 실측 파일 크기)

| 모델 | fp32 | fp16(O4) | int8 | 차원 |
|---|---:|---:|---:|---:|
| bge-base-en-v1.5 (현행) | 436MB | · | · | 768 |
| **multilingual-e5-base** | 1,110MB | 555MB | **279MB** | **768** |
| multilingual-e5-small | 470MB | 235MB | 118MB | 384 |
| LaBSE | 1,882MB | · | · | 768 |
| bge-m3 | ~2.3GB | · | · | 1024 |

- 다국어 모델은 어휘가 250k라 임베딩 행렬만으로 커진다. **fp32 로는 1024MiB 컨테이너에 어느
  것도 안 들어간다.** int8 본은 전부 모델 소유자 리포의 공식 export 다.
- 현재 프로비전 차원은 768(`store/config.py` `EMBEDDING_DIM_BY_MODEL["local-en"]`). **차원이
  바뀌면 재프로비전 + 전량 재임베딩**이다.

## 불변식 — 이걸 깨는 설계는 답이 아니다

- **오프라인·무클라우드**: 외부 임베딩 API 금지(원문이 서버 밖으로 나가면 안 된다). 확장은
  오프라인 번들(G0). 데스크탑은 네트워크 없이 동작해야 한다.
- **R4 단일 writer**: 모든 쓰기는 잡 큐의 단일 워커 스레드로 직렬화된다. 조회 경로는 쓰기
  트랜잭션을 요구하는 것을 하면 안 된다 — DML·DDL 은 물론 `LOAD EXTENSION` 도 포함이다
  (2026-07-31 실측으로 확정. 어긴 결과 검색 49%·저장 26% 실패).
- **§11 P2 게이트**: 긴 쓰기 잡이 도는 중에도 조회가 응답해야 한다. 읽기를 큐·락에 태우지 마라.
- **Chunk = SSoT(D45)**: 청크가 정본 단위다. 재청킹은 정본을 다시 쓰는 일이다.
- **웹 3중 등재**: 새 라우트는 사이드카(`api/server.py`) · 허브 `_PROXY_OPS`
  (`vinc_mcp/http_server.py`) · 계정 프록시 `GET_OPS`/`POST_OPS`
  (`Vinc-homepage/account/src/routes/graph.ts`) 세 곳에 동시 등재. 하나라도 빠지면 그 기능만
  웹·MCP 에서 조용히 죽는다(실사고 전례). 강제하는 테스트는 **없다**.
- **벡터 공간 동일성**: 서로 다른 모델의 벡터가 같은 색인에 섞이면 **에러 없이** 검색만 나빠진다.
  ONNX 전환 때는 코사인 대조(최악 0.99999994)로 막았다. 모델을 바꾸면 그 방어가 성립하지 않는다.
- **화면 노출 텍스트에 em-dash(—) 금지**. 대체는 `·`, 괄호, 콜론.
- 결정은 `Vinc-docs/vinc/D/vinc.d.atlas-visual-language.md` §6 DEC 대장에 등재된다.

## 분석할 것

### ① 토큰 기준 청킹

- **데스크탑 문제가 핵심이다.** 청킹은 `put_doc` 경로에서 일어나고, 그건 데스크탑 사이드카에서도
  돈다. 거기엔 토크나이저가 없다. 선택지를 비교하라:
  (a) `tokenizers` 휠을 사이드카에 번들 — 실제 증가 용량은? PyInstaller 정적분석 사각은?
      vocab 파일(약 700KB)은 어떻게 싣나? torch 의 2.7GB 와는 다른 급이라는 게 사실인가?
  (b) 문자 가중치 근사(한글·CJK 를 더 무겁게 세는 순수 파이썬) — 정확도는? 근사가 빗나가
      512 를 넘기면 다시 잘린다. 안전 마진을 얼마로 잡아야 하나?
  (c) 청킹을 허브에서만 토큰 기준으로 — 데스크탑과 허브의 청크가 달라진다. `content_hash`
      와 sync 에 무슨 일이 일어나는가?
- **기존 문서 이행**: 재청킹하면 `chunk_id`·`content_hash` 가 바뀐다. 그 파급을 추적하라 —
  `api/docsync.py`(문서 sync), `api/sync.py`(그래프 sync, `SYNC_FIELDS` 허용목록),
  needs_embed 재설정, 원문 출처 색인, 편집 중 문서. **모든 기기가 한꺼번에 재청킹하면 sync 폭풍이
  나는가?**
- 목표 토큰 수는? 512 를 꽉 채울 것인가, 여유(예: 480)를 둘 것인가. 오버랩은 둘 것인가
  (`engines/embed_pipeline.py`의 `CHUNK_OVERLAP` 은 chonkie 1.6.8 이 인자를 제거해 죽은 상수였다).
- `engines/embed_pipeline.py` 의 `CHUNK_SIZE = 500`(cl100k_base 기준)과 `docstore` 의 2000자는
  무슨 관계인가. 두 청킹 경로가 공존하는가, 하나는 죽었는가.
- 문단·행 경계 선호(`plan_boundaries`)를 토큰 기준에서도 유지할 수 있는가.

### ② 벡터 검색 활성화

- **어느 표면에서 쿼리 임베딩을 만들 것인가.** 위 확정 사실대로 인프로세스 임베더가 없다.
  선택지를 비교하라:
  (a) 허브 사이드카 프로세스에 ONNX 임베더를 인프로세스로 올린다 — 그러면 요청 경로가 모델
      440MB 를 상주시킨다. 1024MiB 안에서 가능한가? 워커와 **중복 적재**되는가?
      한 프로세스로 합치면 R4·P2 게이트에 무슨 일이 생기는가?
  (b) 워커를 쿼리도 받는 루프백 서비스로 승격 — 사이드카가 `POST /embed/encode` 로 부른다.
      조회 요청 안에서 다른 프로세스를 기다리는 셈인데 P2 게이트에 맞는가? 타임아웃은?
  (c) 데스크탑은 FTS 전용으로 두고 **웹·허브에서만** 벡터 검색 — 기능이 표면마다 달라진다.
      제품으로 수용 가능한가? UI 는 그 차이를 어떻게 정직하게 말하나?
  (d) 데스크탑에도 작은 ONNX 모델 번들(예: multilingual-e5-small int8 118MB) — 앱 용량 증가와
      오프라인 불변식의 관계는?
- **BGE 비대칭 검색**: bge-*-v1.5 는 passage 는 무접두, query 는 지시 접두
  (`Represent this sentence for searching relevant passages:`)를 권고한다. 현재 리포에 그 문자열은
  0건이다. 접두를 넣으면 **기존 저장 벡터와의 관계**는 어떻게 되나(passage 쪽은 안 바뀐다).
  e5 계열은 `query:` / `passage:` 접두를 쓴다 — 모델을 바꾸면 이 규약도 바뀐다.
- **인덱스 생명주기 — 여기가 ②의 최대 급소일 수 있다.** `api/docstore.py` 의
  `_drop_indexes_if_any(store)` 를 부르는 곳은 **네 군데이고 전부 문서 쓰기 경로**다:
  `put_doc()`:270 · `delete_doc()`:422 · `reembed_doc()`:568 · `apply_embeddings()`:618.
  즉 **문서를 저장·삭제할 때마다, 그리고 워커가 문서마다 임베딩을 적용할 때마다** 벡터·FTS
  인덱스가 통째로 드롭된다. 재빌드는 하지 않고(:212-213 주석이 명시) **검색 경로의 지연 보장**
  에 위임한다 — 그 지연 빌드는 2026-07-31 부터 잡 큐를 경유하며 타임아웃 60초다
  (`api/server.py` `_at_write_point`).
  따져야 할 것:
  - 지금은 FTS 만 쓰는데도 이 구조다. 벡터 검색을 켜면 **HNSW 인덱스까지** 매 저장마다
    드롭·재빌드되는가? HNSW 빌드 비용은 BM25 와 급이 다르다. 코퍼스 규모별로 산정하라.
  - 워커가 한 라운드에 문서 16개를 처리하면 드롭이 16회 일어난다. 그 사이에 들어온 검색은
    매번 "인덱스 부재 → 잡 큐로 빌드 요청 → 최대 60초 대기" 를 밟는가?
  - 이 설계가 정당했던 전제(인덱스가 걸린 `embedding` 열은 수정 불가)가 지금도 참인가.
    드롭 없이 갱신할 방법이 ladybug 0.18 에 있는가.
  - 대안: 적재 구간 전체를 감싸 **한 번만** 드롭하고 끝에 한 번 빌드하는 형태로 바꿀 수
    있는가. 그러면 R4·P2 게이트와 어떻게 만나는가.
- 벡터 인덱스가 허브에서 실제로 생성되는지 확인하라(`build_search_indexes`/`ensure_search_indexes`
  가 `dim` 을 받는 조건, 그리고 그 dim 이 어디서 오는지). 지금 생성되지 않는다면 그 이유는.
- RRF 융합(`rrf_fuse`, k=60)은 이미 있다. 켰을 때 결과 품질을 **무엇으로 판정**할 것인가 —
  평가셋이 없다. 최소한의 판정 수단을 설계하라.

### ③ 다국어 모델 전환

- 후보 선정: 위 표를 출발점으로 하되 **2026년 현재 최신 정보를 웹으로 확인**하라(표는 2026-07-31
  기준이다). 판정 기준은 (i) 768 차원 유지 가능한가 (ii) 컨테이너에 들어가는가
  (iii) 공식 ONNX 가 있는가 (iv) 한국어 검색 품질 근거가 있는가(MTEB 등 출처 있는 수치).
- **int8 양자화의 실효성**: `model_qint8_avx512_vnni.onnx` 는 AVX512-VNNI 명령어를 탄다.
  Azure Container Apps 의 CPU 가 그걸 지원하는지 확인하라(확인 방법까지 제시). 미지원이면
  대안(`quint8_avx2` 등)이 그 리포에 있는가.
- int8 은 무손실이 아니다. 이 프로젝트는 벡터 공간 동일성을 코사인 대조로 검증해 온 규율이 있다.
  **모델 전환 시 무엇을 무엇과 비교해야 의미가 있는가**를 설계하라(같은 모델의 fp32↔int8 비교와,
  구 모델↔신 모델 비교는 목적이 다르다).
- **이행 비용**: 전량 재임베딩이 불가피하다. 허브는 단일 writer 이고 5분 간격 워커다.
  문서 N개·청크 M개일 때 소요 시간과 그동안의 검색 상태(구 벡터·신 벡터 혼재)를 산정하라.
  혼재를 피하는 전략(버전 태깅, 이중 색인, 일괄 교체)의 비용을 비교하라.
- 데스크탑은 이 전환에서 어떻게 되는가(모델을 안 싣는다면 무영향인가).

## 순서 자체를 의심하라

발주자가 제시한 ①→②→③ 이 최적이 아닐 수 있다. 특히 따져 볼 것:

- ③을 먼저 정하면 ①의 목표 토큰 수와 토크나이저 선택이 달라지지 않는가?
  (e5 는 XLM-R sentencepiece 라 토큰 밀도가 완전히 다르다. 영어 전용 토크나이저 기준으로
  청킹해 놓고 나중에 모델을 바꾸면 **또 재청킹**해야 하는가?)
- ②를 켜지 않은 채 ①·③을 하면 효과를 측정할 방법이 없다. 그렇다면 ②가 먼저여야 하지 않는가?
- 재청킹과 재임베딩은 둘 다 전량 작업이다. **한 번에 묶는 것**이 두 번 도는 것보다 싸지 않은가?
- 반대로, 지금 벡터 검색이 꺼져 있다는 사실이 **아무것도 급하지 않다**는 뜻일 수도 있다.
  그렇다면 가장 싼 순서는 무엇인가?
- ①의 근거가 생각보다 약할 수 있다. 영어 손실은 5.7% 이고, 이 리포의 시드 코퍼스는
  **125문서 중 124개가 영어 우세**다(실측). 즉 ①의 이득은 지금 코퍼스가 아니라 **앞으로 들어올
  한국어 문서**에 달려 있다. 발주자의 실제 사용 언어 비중을 확인하지 않고 ①의 우선순위를
  정하지 마라 — 이건 측정이 아니라 **발주자에게 물어야 할 것**이다.
- 🔴 **①의 이득은 ②가 켜지기 전까지 0 이다.** 이건 확인된 사실이다:
  - 청킹은 **무손실**이다(`api/docstore.py:80` — `"".join(text[b_i:b_{i+1}]) == text` 가 정의상 성립).
    청크는 2000자 전문이 그대로 스토어에 들어간다.
  - FTS 는 그 **전문**을 색인한다(`store/embedded_backend.py:461,493` — `CREATE_FTS_INDEX
    ('Chunk','chunk_ft',['text'])`).
  - 512 잘림이 일어나는 곳은 **딱 한 군데**, 임베딩 워커의 토크나이저다
    (`packaging/embed_worker.py:108` `tok.enable_truncation(MAX_TOKENS)`).
  - 그리고 그렇게 만든 벡터는 아무도 읽지 않는다(위 🔴 절).
  ⇒ 즉 잘림은 **벡터에만** 영향을 주고, 벡터는 죽어 있다. 지금 사용자가 겪는 검색은 100% FTS 이고
  거기엔 손실이 없다. 발주자가 제시한 ①→②→③ 은 **이득이 0 인 작업을 먼저 하는 순서**일 수 있다.
  이 사실을 순서 판정의 출발점으로 삼아라.

## 특히 주의할 함정 (전례 있음)

- **조용한 강등**: 이 리포는 "에러 없이 기능만 죽는" 사고가 반복됐다(이관 때 임베딩 워커 소멸,
  구조 진단 상시 강등, 웹 프록시 미등재). 설계에 "실패하면 어떻게 **보이는가**"를 반드시 넣어라.
- **Binder 함정**: 특정 플랜에 없는 컬럼을 투영하면 `/graph` 전 레벨이 죽는다.
- **`_DocMeta` 에는 본문도 domain 도 없다.** 주제는 `(Chunk)-[:MENTIONS]->(Concept).domain`
  다수결로 유도한다.
- 스키마 변경은 `store/embedded_schema.py` `SCHEMA_VERSION`(현재 7) + `migration_statements`
  의 `ALTER ADD ... DEFAULT` 멱등 흡수 패턴을 따른다.
- 테스트는 임베딩 스택이 없는 CI 에서도 **skip 되면 안 된다**(조용한 skip 이 이 리포의 상습 함정).

## 산출물

1. **순서 판정** — ①→②→③ 이 옳은가. 아니면 무엇이 옳은가. 근거와 함께.
2. **단계별 설계** — 각 단계의 선택지 표(비용·효과·위험·되돌릴 수 있는가)와 권고안 1개.
   권고안은 코드 변경 지점을 file:line 수준까지 지목한다.
3. **이행 계획** — 기존 데이터에 무슨 일이 일어나는가. 다운타임·sync 영향·롤백 가능성.
4. **검증 설계** — 각 단계가 성공했는지 **무엇으로 판정**하는가. 평가셋이 없다는 전제에서
   최소한의 판정 수단. 회귀 테스트로 못박을 것.
5. **발주자 결정 지점** — 네가 정하면 안 되는 것들을 질문 목록으로. 각 질문에 선택지와
   그 선택이 뒤에 미치는 영향을 붙인다.
6. **미확인 목록** — 확인하지 못한 것과, 확인하려면 무엇이 필요한지(권한·자격증명·측정 환경).

한국어로 작성한다. 표를 쓰면 읽기 좋은 곳엔 표를 쓴다. 분량은 필요한 만큼.

---

## 부록 — 실측 재현 절차

프롬프트를 받은 쪽이 수치를 의심할 때 쓸 것. 전부 모델 본체 없이 돈다.

```bash
# 토크나이저만 받는다(약 700KB, 440MB 모델 아님)
curl -sL -o tok.json https://huggingface.co/BAAI/bge-base-en-v1.5/resolve/main/tokenizer.json
```

```python
# 실제 코퍼스의 청크 토큰 분포. ⚠ enable_truncation 을 **켜지 마라** — 켜면 p90·최대가 둘 다
# 512 로 나와 상한을 데이터로 착각한다. 끄고 재면 중앙 469 · p90 605 · 최대 1400 · 31.5% 초과 ·
# 전체 토큰의 5.7% 손실.
# cd Vinc && uv run --python 3.13 --with tokenizers python
import glob, statistics, sys; sys.path.insert(0, '.')
from tokenizers import Tokenizer
from api.docstore import chunk_doc
tok = Tokenizer.from_file('tok.json')
lens = []
for p in glob.glob('starter-packs/**/*.md', recursive=True):
    t = open(p, encoding='utf-8', errors='replace').read()
    if not t.strip():
        continue
    chs = [c if isinstance(c, str) else (c.get('text') or '') for c in chunk_doc(t)]
    lens += [len(e.ids) for e in tok.encode_batch(chs)]
lens.sort()
print(len(lens), statistics.median(lens), lens[int(len(lens) * .9)], max(lens),
      f'{sum(1 for x in lens if x >= 512) * 100 / len(lens):.1f}% 잘림')
```

```python
# 언어별 문자/토큰 밀도 — truncation 을 켜지 않아야 진짜 토큰 수가 나온다(켜면 512 에서 잘려
# 밀도가 아니라 상한을 재게 된다). 결과: 한국어 0.69 · 일본어 0.99 · 중국어 0.99.
# 영어는 아래 합성 표본이 6.91 로 나오지만 **실제 코퍼스는 4.3** 이다 — 같은 문장을 반복한
# 표본이라 흔한 기술 어휘가 단일 토큰으로 잡혀 과대평가된다. 인용은 4.3 을 쓸 것
# (그 값은 바로 위 코퍼스 스니펫의 중앙 469토큰 / 2000자 에서 나온다).
from tokenizers import Tokenizer
tok = Tokenizer.from_file('tok.json')
S = {
    '영어': 'The Hamiltonian formulation of classical mechanics provides a natural bridge '
            'to quantum theory through the correspondence principle. ',
    '한국어': '해밀토니안 형식의 고전역학은 대응원리와 정준양자화 절차를 통해 양자이론으로 '
             '가는 자연스러운 다리를 놓는다. ',
    '일본어': 'ハミルトニアン形式の古典力学は、対応原理と正準量子化の手続きを通じて量子論への'
             '自然な橋渡しとなる。',
    '중국어': '哈密顿形式的经典力学通过对应原理和正则量子化过程为量子理论提供了自然的桥梁。',
}
for k, s in S.items():
    txt = s * 8
    n = len(tok.encode(txt).ids)
    cpt = len(txt) / n
    print(f'{k:<5} 문자/토큰 {cpt:5.2f} · 2000자→{int(2000/cpt):5,}토큰 · '
          f'512토큰에 담기는 문자 {int(512*cpt):5,} · 버려짐 {max(0,1-512*cpt/2000)*100:3.0f}%')
```

```python
# 한글이 왜 자모로 쪼개지는가 — 어휘에 음절 토큰이 0개임을 직접 확인
import json, unicodedata
d = json.load(open('tok.json', encoding='utf-8'))
v = d['model']['vocab']
print('normalizer:', d['normalizer'])          # BertNormalizer lowercase=true,
                                               # strip_accents=null → lowercase 값을 따라 true
                                               # → NFD 분해가 적용된다
print('음절(가-힣) 토큰', sum(1 for t in v if any('가' <= c <= '힣' for c in t)))  # 0
print('자모(U+1100~) 토큰', sum(1 for t in v if any('ᄀ' <= c <= 'ᇿ' for c in t)))  # 70
print('NFD 배수:', len(unicodedata.normalize('NFD', '양자역학')) / len('양자역학'))         # 2.75
```

```bash
# 모델 크기·ONNX 변형 목록 (HuggingFace API)
curl -s "https://huggingface.co/api/models/intfloat/multilingual-e5-base?blobs=true"
```

```bash
# 벡터 검색이 꺼져 있음을 확인
grep -rn "query_embedding" Vinc/api/*.py        # 정의·전달만, 공급자 0건
grep -rn "q\.search(" Vinc/api/*.py             # server.py:270, cli.py:231 둘 다 미전달
```
