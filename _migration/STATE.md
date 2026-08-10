# Graph Redesign Migration — State

> 세션 간 인수인계 단일 원천. 각 세션은 시작할 때 이 파일을 읽고, 끝날 때 갱신한다.
> 계획 원본: `C:\Users\weg1456\.claude\plans\shimmying-crunching-parrot.md`

## 지금 어디인가

**S1 (P0 + P1) 진행 중** — 2026-08-06

| 단계 | 상태 |
|---|---|
| P0 동결·export·스냅샷 | **완료** |
| P1 임베딩 워커 | 조사 완료 · **수리 분리**(발주자 결정) |
| P2 학습자료 삭제 | **완료** (문서 107→54, 노드 5 삭제) |
| P3 체계 확정 | **완료** (SCHEME.md + 원장 2종, 기계 검증 통과) |
| P4 재작성 | **문서 54/54 · 노드 198/198 완료**(린트 둘 다 0) · 남은 것은 **엣지 재배선 판정** 하나 |
| **P5 적재** | **완료 (2026-08-08)** — 아래 |
| P6~P8 | **판단 대상 재측정 완료** — 아래 |

## P5 완료 (2026-08-08)

**왜 이제야 했나.** P7(구본 삭제)이 `Record 94 분류`·엣지 재배선 223건을 기다리는
동안 P5 까지 같이 묶여 있었다. 그런데 이 문서 자신이 "롤백 기반" 절에 적어 뒀듯
**신 doc_id 는 구 id 와 전부 다르다**(`vinc.canon.*` 대 `vinc.a.*`). 적재는 구본을
건드리지 않으므로 P7 을 기다릴 이유가 없었다. 두 달치 재작성이 파일에만 앉아 있었고,
세션 인계에는 "진행 중"으로만 적혀 있었다.

실행(`load_p5.py` · `purge_learning.py`):

| 단계 | 결과 |
|---|---|
| 노드 제자리 재작성(keep 182 + rewrite 10) | **192/192** · created 0 · 경고 0 |
| drop 노드 삭제 | **6/6** (학습자료 잔여 concept 5 + 낡은 record 1) |
| 신 문서 적재 | **54/54** |
| 구 문서 삭제 | **54/55** (1건은 이미 부재) |
| 🔴 되살아난 학습자료 삭제 | **53/53** — 아래 |

**드러난 것: P2 가 되감겨 있었다.** 이 문서는 "P2 학습자료 삭제 완료(107→54)"라고
적고 있었는데, 2026-08-07 FTS 손상 복구가 그 이전 시점 DB 사본으로 되돌리면서
**학습자료 53건이 전부 되살아나 있었다**. 원장에 그 사실이 안 적혀 있어서 "완료"가
거짓이 된 채로 이틀을 갔다. 재해 복구가 이행 단계를 되감을 수 있다는 것 자체를
여기 적어 둔다 — 다음에 복구를 하면 **어느 단계까지 되감겼는지 다시 세야 한다**.

식별은 `origin` 이 `WORKSPACES/learning/` 으로 시작하는 것으로 했다(doc_id 가 해시라
접두로는 못 가른다).

**검증**: 문서 118 → **65**(학습자료 0). 도메인에서 `vinc/timeline`(에피소드 48건이
몰려 있던 노드종류-도메인 오류)이 **사라지고** `engineering` 50 · `product` 27 ·
`security` 37 이 생겼다. 신본 검색·`[[...]]` 상호참조 동작 확인.

## 엣지 판단 대상 재측정 (2026-08-08)

"판단 대상 223건"은 **2026-08-06 인벤토리** 기준이었다. P5 가 문서 54 ·
doc-concept 39 · 학습자료 53 · 노드 6 을 지웠으므로 다시 셌다(`edge_census.py`).

| | 08-06 | 라이브(P5 후) |
|---|---|---|
| 판단 대상 | 223 | **160** |
| 그중 문서 스텁이 낀 것 | 66 | **16** |

66→16 은 결정이 아니라 **P5 의 부산물**이다. 그 스텁들이 바로 P5 에서 지운 고아
doc-concept 이었다. 남은 160 의 구성: 개념↔개념 144 · 개념↔문서 15 · 문서↔문서 1.

**개념↔개념 144 는 판단 대상이 아니다.** 노드 id 가 P5 를 통과하며 안 바뀌었으므로
그 관계들은 그대로 실려 있고, 08-06 의 우려는 문서 스텁 쪽이었다.

**남은 16 은 전부 살아 있는 문서를 가리킨다**(고아 아님). 08-06 분석은 이들이
`Chunk-MENTIONS-Concept` 와 중복이라 판정했지만 그 판정은 재가되지 않았고, GRAPH.md
가 "자기 제안을 스스로 재가하지 마십시오"라고 못박고 있어 자동 삭제하지 않았다.

### 🔴 드러난 결정 하나: 문서 상속이 그래프에 없다

라이브 `INHERITS_FROM` 이 **0건**이다. 신 코퍼스 54건 중 **53건이 frontmatter 로
`inherits_from` 을 선언**하고 `inherits_map.tsv` 에 복원까지 돼 있는데, 그래프에는
그 엣지가 하나도 없다. 원인: `vinc_doc_put` 은 청크와 MENTIONS 만 만들고 doc-stub
개념을 안 만든다 — 실측으로 `concept:doc.vinc.canon.*`·`spec.*`·`research.*` 가 0건이다
(남은 doc-concept 17건은 전부 구작업이거나 수동 생성분).

**이게 왜 결정인가**: MENTIONS 는 "이 문서가 이 개념을 언급한다"를 텍스트에서 파생한다.
그런데 `inherits_from` 은 **문서와 문서 사이**의 관계라 MENTIONS 로는 표현이 안 된다.
지금 상태에서는 "vinc.canon.graph-conventions 는 무엇 위에 서 있나"를 `vinc_path` 로
따라갈 수 없다. 선택지는 둘이다:

1. **doc-stub 개념 54개를 만들고 상속을 엣지로 물린다** — 탐색 가능해지지만 08-06
   분석이 경계한 "같은 사실의 두 표현"이 개념↔문서 축에 다시 생긴다
2. **문서 상속은 frontmatter 에만 둔다** — 그래프는 개념 축만 다루고, 상속은 문서를
   읽을 때만 보인다(지금 상태)

발주자 결정 사항이다.

## P5 꼬리 정리 (2026-08-08, 같은 세션)

원장 밖 노드 33건이 구 도메인을 달고 있었다(원장은 08-06 작성이라 그 뒤 노드가 없다).
`remap_domains.py` · `prune_doc_concepts.py` 로 닫았다.

| 처리 | 건수 | 근거 |
|---|---|---|
| `vinc/architecture` → `vinc/engineering` | 자동 | 원장 실적 20/20 만장일치 |
| `vinc/mcp` → `vinc/engineering` | 자동 | 원장 실적 18/18 만장일치 |
| `vinc/web` → infra 2 · security 1 | 사람 판정 | 원장에서 갈리는 도메인(product 7·engineering 7·infra 4)이라 내용으로 판정 |
| 잔여 doc-concept 삭제 | **39건** | 아래 |

**doc-concept 잔여.** 문서를 적재하면 `concept:doc.<doc_id>` 손잡이가 함께 생기는데,
문서 삭제가 그것까지 캐스케이드하지 않는다. 구 문서 54건을 지우자 가리키는 것이 없는
개념 39개가 남았다. 판정은 `concept:doc.X` 인데 문서 `X` 가 없으면 잔여.

**결과: 구 도메인 0.** 남은 것은 신 7종뿐이다 —
design 81 · engineering 71 · infra 61 · governance 38 · security 37 · product 27 · ai 8.

### 🔴 열거를 두 번 막은 것: 라이브 diagnose 의 무음 절단

`vinc_diagnose(kind=components)` 는 `member_limit` 을 무시하고 members 를 **50개로
자르면서 잘렸다는 표시를 안 준다**(실측: `size: 185` 인데 members 50건). 그걸 모르고
전수 순회를 돌려 개념 189 중 54 만 보고 "다 봤다"고 착각했다 — 실제로 그 상태로 한
번 매핑을 돌렸고 9건이 남았다.

우회는 둘이다: ① 원장(파일이라 안 잘린다)을 시드에 깔고 ② **커뮤니티 축**을 합친다.
WCC 컴포넌트는 하나가 거대해(144) 상한에 걸리지만 Louvain 군집은 12개로 잘게 나뉘어
(최대 18) 상한에 안 닿는다. 같은 절단 버그를 우회하는 두 번째 창이다.

⚠ 이 버그의 수정(F6 — `member_limit` 인자 + `members_truncated` 표식)은 **이미
작성돼 로컬 main 에 있고 미푸시다**. 배포하면 이 우회가 필요 없어진다.

## 확정된 사실 (계획서 수치 정정 포함)

- 문서 **107건 = Vinc 54 + 학습자료 53**. 계획서 초안의 "57 / 50" 은 **틀렸다**. 매니페스트(`inventory/doc-manifest.tsv`)가 정본
- Vinc 문서 54건 중 `d2b6633ee1151a272` 하나만 `vinc.*` 체계 밖(해시 ID, origin 은 `Vinc/README.md`). 재작성 때 정식 ID 부여 대상
- 노드 314(Concept 172 + Record 94 + Episode 48), 엣지 약 265
- 도메인 8종. Episode 48개 전부가 `vinc/timeline`(노드 종류를 도메인으로 쓴 오류)
- Louvain 군집 12개가 도메인 8개보다 정합. 계정정책·요금·쿼터 군집이 `vinc/mcp` 로 오배정 = product 축 부재

## 롤백 기반 (3층 → **2층**, 2026-08-08 정정)

1. ~~**Azure Files 공유 스냅샷** `2026-08-05T23:40:45.0000000Z`~~ — 🔴 **2026-08-08 삭제됨.
   복구 불가.** E2 게이트용 임시 스냅샷을 정리하면서 `hub-data` 의 스냅샷을 **전부**
   지우는 필터를 썼다(에이전트 실수). 계정에 공유 소프트삭제가 7일로 켜져 있으나
   그것은 삭제된 **공유**를 덮지 스냅샷 직접 삭제는 못 덮는다(ARM `$expand=deleted`
   조회 0건으로 확인).

   **실질 피해는 제한적이다.** 그 스냅샷이 담고 있던 파일들은 지금도 공유에 **live 파일로
   남아 있다**: `db`(08-02 체크포인트) · `db.bak-20260804` · `db.wal.bak-20260804` ·
   `db.recovered`(08-05 16:56) · `db.recovered.bak-20260807` · `db.recovered.wal.bak-20260807`.
   사라진 것은 "일관 시점 이미지"이고, 그 안의 WAL 은 어차피 FTS 손상으로 재생 불가임이
   2026-08-07 샌드박스 실증으로 확정돼 있었다.

   ⚠ **교훈**: 공유 스냅샷 정리는 **시각으로 정확히 지목**해서 지운다. `hub-data` 의
   스냅샷에는 이 이관의 복원 지점이 섞여 있다. 새 스냅샷을 뜨면 그 시각을 먼저 적어 두고
   그 하나만 지울 것.
2. **문서 전문 export** — `_migration/export/*.md` (54건 목표). 메타 블록 + 원문 바이트 그대로.
   여러 건은 SHA-256 으로 `content_hash` 대조 확인. 구 doc_id 재-put 으로 논리 복원 가능
3. **병존 설계** — 신 ID 는 구 ID 와 전부 다르므로 P5~P6 실패는 신본만 삭제로 청산

## P1 임베딩 워커 — 조사 결과 (수리는 별도 과제)

**걷어낸 오답 둘**
- "`VINC_EMBED=off` 로 꺼져 있다" → 코드가 `${VINC_EMBED:-on}`. 미설정 = 켜짐. 워커는 기동했다
  (`[entrypoint] embed worker started (pid=32, interval=300s)`)
- "모델 다운로드가 막혔다" → 모델은 **이미지에 구워져** 있다(`/srv/vinc/models/e5-int8-rr.onnx`
  278MB + `tokenizer.json` 17MB, 실물 확인). 워커도 `VINC_EMBED_MODEL_PATH` 를 정상적으로 읽어
  다운로드를 건너뛴다. 컨테이너의 외부 도달도 정상(HuggingFace API 200)

**남은 사실**
- `/data/embed-model` 부재 + `[embed] model ready` 로그 없음 = **모델이 한 번도 적재된 적 없다**
- `_load()` 는 지연 로드라 일감이 있을 때만 도는데 첫 라운드가 `pending=133` 을 받았으니 일감은 있었다.
  그런데 `_load()` 첫 실행문인 `os.makedirs(CACHE)` 가 만들었어야 할 디렉터리가 없다
  → 실패 지점은 그 직전 import 4줄(`numpy`·`onnxruntime`·`huggingface_hub`·`tokenizers`)
- `pip show` 로는 onnxruntime 1.28.0 · huggingface_hub 1.26.0 확인. numpy·tokenizers 는 조회가
  빈손이었으나 exec 가 불안정(500·429)해 **부재로 단정할 수 없다**

**확정 못 한 이유**: `az containerapp logs show --tail 300` 이 18줄만 반환(로그 조회 한계) ·
`az containerapp exec` 가 429 로 막힘

**다음 수단(안전)**: 워커에 예외 로깅 보강 → 허브 재배포 → 다음 라운드 로그 확인.
**피할 것**: 컨테이너 안에서 `embed_worker.py --once` 전경 실행 — 1Gi 안에 모델이 두 벌 올라가
OOM 으로 MCP 까지 죽일 수 있다(Dockerfile 이 명시 경고하는 실패 모드)

**타이밍**: P2 가 학습자료 53건을 지우면 pending 이 절반으로 줄어 **최종 코퍼스로 검증**할 수 있다.
그때 하는 편이 낫다.

## 산출물 위치

```
_migration/
  STATE.md                     이 파일
  export/{doc_id}.md           문서 전문 사본 (메타 블록 + 원문)
  inventory/doc-manifest.tsv   문서 107건 목록 + vinc/learning 분류
  inventory/nodes.tsv          노드 인벤토리 (작성 중)
  inventory/edges.tsv          엣지 인벤토리 (작성 중)
  inventory/summary.md         인벤토리 요약 (작성 중)
```

## 다음 세션이 할 일

1. export 잔여분 확인(54건 채워졌는지) 후 **P0 게이트 판정**
2. 인벤토리 산출물 검토 — 특히 **DEC 명명 패턴 분류**(이행 원장의 입력)와
   **P2 삭제 후보 노드 목록**
3. P2 실행: 학습자료 문서 53 + 딸린 노드 삭제. 삭제 전 목록 리뷰가 실질 가드다(비가역)
4. P3 착수: 도메인 7종·문서 계층·이행 원장

## 주의 (실행 중 지킬 것)

- **쓰기 도구를 조사 목적으로 쓰지 마라.** P2 이전까지 원격 그래프는 읽기 전용이다
- fragment MERGE 3함정: 완전 페이로드만(스텁은 title 을 id 로 덮는다) · 엣지는 양끝 노드를 같은
  페이로드에 동봉 · wire 타입 6종만
- 개념 먼저, 문서 나중 (put_doc 이 put 시점 존재 개념과만 링크)
- 문서의 topic·대표개념은 **파생값**이다. 문서를 고쳐서 안 고쳐진다 — 개념 domain 과 언급 위생으로만

---

## P0 게이트 판정 (2026-08-06)

| 게이트 | 결과 |
|---|---|
| 문서 전문 export 54/54 | **통과** |
| 메타 블록 + `verified: true` 전건 | **통과** |
| SHA-256 대조 | 서브에이전트 3팀이 담당분 독립 검증, 전건 일치 |
| 인프라 스냅샷 | **통과** — `vincstorage01` / `hub-data` / `2026-08-05T23:40:45.0000000Z` |
| 노드·엣지 인벤토리 | 진행 중 (별도 에이전트) |

export 총량 1.2MB. 잔여는 인벤토리뿐이며, 그것이 나오면 P0 종료.

## 문서 코퍼스 실측 (P3 이행 원장 입력, 2026-08-06)

산출: `inventory/doc-frontmatter.tsv` (54행 · 본문을 읽지 않고 프론트매터만 추출)

**코퍼스가 두 모집단으로 갈려 있다.**

| 모집단 | 건수 | 특징 |
|---|---|---|
| A/B/C/D 계층 | 41 | 프론트매터 보유(layer·authority·status·inherits_from) |
| 계층 밖 반입 | **13** | **프론트매터 없음.** ops 런북 5 · plans 4 · Vinc 리포 3(README·CHANGELOG·emails/ARCHITECTURE) · 대화 산출물 1 |

즉 신 체계는 이름만 바꾸는 것이 아니라 **13건에 메타데이터를 처음으로 부여**해야 한다.

**계층 분포**: D 22 · C 12 · B 2 · A 2 · ops 3 · 없음 13
→ D(설계)에 절반이 몰려 있다. 신 체계의 spec/research 분리가 이 쏠림을 푸는 축이다.

**status**: draft **39** · final 2 · 없음 13
→ SSoT 를 자처하는 코퍼스의 **72% 가 draft** 다. 신 체계에서 status 를 실제로 운용할지,
아니면 폐기하고 authority 하나로 갈지 결정이 필요하다.

**authority 어휘가 통제되어 있지 않다**: `SSoT` 26 · `research-input` 11 · `inventory` 1 ·
`SSoT (계획)` 1 · `SSoT (as-built)` 1 · `Delta` 1 · 없음 13
→ 괄호 변형이 제각각. 신 체계는 닫힌 어휘여야 한다.

**상호참조 `[[...]]`**: 총 **351회** · 고유 대상 **46개** · 그중 **9개가 깨져 있다**
```
D11-R2 §10            (문서가 아니라 절 참조)
PLAN-embedding-retrieval   (구 파일명 — 정본은 vinc.plan.embedding-retrieval)
WEB-vincs-app              (구 파일명 — 정본은 vinc.c.web-vincs-app)
Vinclums/Vinc docs/MIGRATION.md  (옛 조직명 + 존재하지 않는 파일)
weg.c.memory-standalone    (다른 프로젝트 문서)
vinc-deployment-policy · vinc-web-parity-proxy-allowlist · windows-embedded-test-fails
                           (어시스턴트 메모리 슬러그가 문서 참조로 새어 들어옴)
위키링크                   (설명문 안의 예시 문자열)
```
→ 재작성 때 전량 신 doc_id 로 치환하고, 린트가 "모든 대상 ∈ 신 doc_id 집합 ∪ 외부 허용목록"을
강제해야 한다. 절 참조(`§`)와 예시 문자열은 링크 문법에서 빼는 규칙도 필요하다.

## 노드·엣지 인벤토리 실측 (2026-08-06)

산출: `inventory/nodes.tsv`(310행) · `inventory/edges.tsv`(467행)

**노드 310 / 314**

| kind | 개수 | 비고 |
|---|---|---|
| concept | 178 | 주 컴포넌트 173 + 고아 5 와 정확히 일치 → 누락 없음 |
| record | 78 | **16개 결손** (아래 사유) |
| episode | 48 | 완전 |
| bridge | **6** | 계획서 집계에 없던 4번째 종류 |

도메인: design 76 · timeline 48 · infra 41 · governance 35 · architecture 30 · security 27 ·
mcp 22 · web 19 · 빈값 12

**엣지 467** (계획서의 "약 265" 는 개념-개념만 센 수치였다. Record/Episode/Bridge 부착 169건이 빠져 있었다)

| 타입 | 개수 |
|---|---|
| relates_to | 179 |
| **attaches_to** | **88** ← 와이어 6종 밖 |
| inherits_from | 75 |
| about | 59 |
| derives_from | 35 |
| connects_via | 22 |
| prerequisite_of | 9 |

dangling ref 0 · `(from,type,to)` 중복 0.

### 재설계 결정이 필요한 구조 사실

1. **`ATTACHES_TO` 88건이 와이어 계약 밖에 있다.** 전량 `record:` → `concept:` 방향이고
   `role: "relates_to"` 를 함께 싣는다. `vinc_ingest` 공개 계약(6종)에는 없다.
   `relates_to` 로 평탄화하면 **부착 의미가 사라지고**, 그대로 두면 7번째 타입이 남는다.
2. **`connects_via` 는 직접 엣지가 아니다.** `bridge:` 노드 6개 + `CONNECTS` 엣지 22개로
   물리화되어 있다. 이 물리화를 유지할지 결정 대상.
3. **DEC 68개가 7가지 명명으로 분열**
   `dec-d{n}` 42 · `dec-gm-{n}` 14 · `dec-d-vis-{a}` 5 · `dec-sec-{n}` 2 · `dec-b{n}` 2 ·
   `dec-ap-{n}` 1 · `dec-d48-t` 1 · 규칙 밖 1(`dec-registry` — 결정이 아니라 대장)
   - **이중 등록**: 가시성 결정이 `dec-d75`/`d76` 와 `dec-d-vis-a`/`b` 양쪽에 있다.
     `d-vis-0·c·d` 는 정수 번호가 없다. 그래프 자신이 `record:dec-numbering-fork` 로 기록해 뒀다
   - **번호 이중 사용 2건**: `dec-d43`(데이터 홈 / Atlas 평면 관점) · `dec-d63`(오버레이 규격 / 경로 튜플)
   - 결번: gm-6 없음, d 계열 불연속
4. **id 접두사 없는 노드는 0건** — 전량 `concept:`/`record:`/`episode:`/`bridge:`.
   계획서가 대비했던 무접두 케이스는 없다

### P2 삭제 후보 — 5개 (전부 고아, 엣지 0)

```
concept:art-chinua-achebe
concept:art-coco-chanel
concept:art-east-west-aesthetics-comparison
concept:art-frida-kahlo
concept:art-john-cage
```
전부 domain 빈 값 · `props.source_file` 이 `WORKSPACES/learning/art/...`.
엣지가 없어 연쇄 영향 없음. **삭제 전 props 별도 export 권장**(aliases·influences·one_liner 보유).

domain 이 빈 나머지 7개는 **삭제 대상이 아니다**: bridge 6개는 구조 노드(지우면 connects_via 22건 소멸) ·
`concept:doc.vinc.ops.deployment` 는 메타가 빈 정상 문서 스텁이라 **채우기** 대상.

### 신규 급소 — Episode.date 가 비문서화 계약이다

최근 Episode **9건의 `date` 열이 null** 인데 props/id 에는 날짜가 있다.
원인을 코드로 확정했다: `vinc_ingest` MCP 스키마의 노드 필드는 `id·type·title·domain·tags·props`
**뿐이고 `date`·`summary` 가 없다.** 그런데 `api/fragment.py` 는 Episode 의 그 둘을 저장한다.
**데이터 경로는 받는데 도구 계약이 숨기고 있어** 어시스턴트가 보낼 줄 모른다.
[[vinc-fragment-api-contract]] 메모리에 적힌 "비문서화 계약" 과 같은 계열이다.
타임라인 뷰 작업 전에 스키마를 고쳐야 한다.

해당 9건: account-policy-decision · ap1-p0-p2-landed · ap1-p3-teams-landed ·
methodology-redesign-research · release-0-7-2-planned · atlas-visibility-verification ·
mark-redesign-benchmark · easyauth-blocks-member-keys · risk-audit

### 인벤토리 결손과 사유

- **Record 16개 미확보** — `vinc_neighbors` 가 **Concept 이웃만 돌려주고 Record/Episode/Bridge 는
  안 돌려준다**(실측: `concept:atlas` neighbors 21 vs vinc_graph degree 27). 도구 한계다.
  보완: 지목된 11개 개념에 `vinc_graph(depth=1)` 10여 회
- **Episode 13개의 about 엣지** — 실제 부재인지 미확인 개념 부착인지 구분 불가
- **엣지 방향 일부 추론** — neighbors 가 in/out 을 합쳐 준다. about·attaches_to·connects_via 는
  방향 고정이라 안전하나, neighbors 로만 얻은 개념-개념 엣지는 재검증 권장

## P3 체계 결정 (발주자 확정 2026-08-06)

| # | 결정 | 내용 |
|---|---|---|
| 1 | `ATTACHES_TO` | **7번째 타입으로 정식화.** GRAPH.md 어휘에 명시하고 `vinc_ingest` 스키마에도 드러낸다. 이미 88건이 동작 중이라 데이터 이행 없음 |
| 2 | `status` | **폐기.** 닫힌 어휘의 `authority` 하나로 통합: canon(불변) · spec(as-built) · research(입력) · record(기록). draft/final 은 아무도 갱신하지 않아 신호가 죽었다 |
| 3 | 결정의 위상 | **사건과 규칙을 분리.** 결정한 사건 = Episode(date 보유) · 지금 유효한 규칙 = Concept. `about` 으로 잇는다 |
| 4 | 식별자 표기 | **`dec` → `adr` 로 교체.** `concept:adr-{domain}-{NNN}`(제로패딩). 사람용 라벨 `D45` 는 props 에 보존. 문서·엣지를 전부 다시 쓰므로 개명 비용은 매핑표 하나다 |

**인용이 가리키는 곳**: 규칙(Concept). Episode 는 출처이지 인용 대상이 아니다.

### ⚠ 결정 3 의 제약 — 날짜가 3분의 1 밖에 없다

DEC 68개 중 **날짜 흔적 보유 15개** · Episode 와 `about` 으로 연결된 것 **2개**.
문서 본문에서 grep 으로 회수되는 것도 **약 15개**(AP-1 D19 D21 D22 D32 D37 D43 D44 D51 D53 D55 D69 GM-1 GM-5 SEC-1).

**처리 방침: 날짜를 지어내지 않는다.** Episode 는 출처이므로 출처를 아는 결정에만 만들고,
나머지는 규칙(Concept)만 둔다. 빠진 Episode 는 **눈에 보이는 결손**으로 남아 나중에 채울 수 있다.
P4 재작성 때 문서를 정독하면 grep 보다 더 회수될 수 있으나, 회수 못 한 것을 추정으로 메우지 않는다.

### 이행 원장이 풀어야 할 DEC 이상 4건

- **이중 등록**: 가시성 결정이 `dec-d75`/`d76` 와 `dec-d-vis-a`/`b` 양쪽에. `d-vis-0·c·d` 는 정수 번호 없음
- **번호 이중 사용**: `dec-d43`(데이터 홈 / Atlas 평면 관점) · `dec-d63`(오버레이 규격 / 경로 튜플)
- **결번**: gm-6 없음, d 계열 불연속
- **`dec-registry`** 는 결정이 아니라 대장 개념 → adr 네임스페이스 밖으로

### 시리즈 문자 → 신 도메인 매핑 (P3 원장에서 확정할 것)

`d`=design · `gm`=governance · `sec`=security · `ap`=product · `b`=? (architecture 였으나
신 체계에서 engineering 인지 governance 인지 모호 — 개별 판정 필요) · `d-vis`=design

## P2 실행 결과 (2026-08-06)

| 항목 | 결과 |
|---|---|
| 문서 | **107 → 54** (53건 삭제, 청크 약 450개) |
| 노드 | art 개념 **5건 삭제** |
| `WORKSPACES/learning/` 잔존 | **0** |
| 고아 | **0** · 단일 컴포넌트(173노드) |
| 실패 | 없음 |

### ⚠ 안전 검증이 내가 말한 것보다 약했다 (교훈)

삭제 전 "5개 전부 엣지 0" 이라고 단언했으나, 실제로는 **4개 엣지가 DETACH DELETE 로 함께 사라졌다**
(`east-west` 만 진짜 0이었다). 근거로 쓴 `edges.tsv` 가 **불완전한 인벤토리**였기 때문이다 —
인벤토리 에이전트 스스로 "`vinc_neighbors` 가 Concept 이웃만 돌려줘 Record 16개를 못 봤다"고
보고했는데, 나는 그 한계를 노드 쪽에만 적용하고 **엣지 쪽에도 같은 구멍이 있다는 것을 놓쳤다.**

결과는 무해했다(사후 고아 0 · 단일 컴포넌트). 그러나 **P7 컷오버는 이번보다 훨씬 큰 삭제**이므로
같은 방식으로 판단하면 안 된다. P7 전에 반드시:
- `vinc_graph(depth=1)` 로 삭제 대상 **전건의 실제 차수**를 재확인할 것(neighbors 로 갈음 금지)
- 인벤토리의 결손 16건을 먼저 채울 것

### 임베딩 카운터는 스테일이 아니다

`embed_migration.pending: 55` 이고 남은 문서는 54건이라 "스테일" 로 보이지만,
**55 = 문서 54 + 관례 문서 `graph.md` 1** 이다. 이전 값 108 = 107 + 1 과 정확히 일관된다.
동시에 이것은 **여태 단 한 건도 임베딩되지 않았다**는 확인이기도 하다(P1 조사와 부합).

## 다음 세션이 할 일 (갱신)

1. **P3 이행 원장 작성** — 이것이 다음 단계다. 담을 것:
   - 구 doc_id 54 → 신 doc_id (canon/spec/research/ops/plan/release) 매핑, N:M 허용
   - DEC 68 → `adr-{domain}-{NNN}` 매핑 + 사람용 라벨 보존 + 이중등록·번호충돌 4건 해소
   - 날짜를 아는 결정(약 15개)의 Episode 분리 대상 지정
   - Record 94 의 keep/rewrite/drop 분류
   - 시리즈 문자 → 신 도메인 매핑(특히 `b` 가 engineering 인지 governance 인지 개별 판정)
2. **인벤토리 결손 보완** — Record 16 + Episode 13의 about 엣지. P7 안전을 위해 필수
3. P4 재작성(3분할: canon+governance/security · spec · research+ops+plan+release)

## P3 완료 (2026-08-06)

산출: `SCHEME.md`(신 체계 정의) · `ledger-documents.tsv`(54) · `ledger-adr.tsv`(68)

**기계 검증 통과**
- 문서 원장 54행, 구 doc_id 가 실제 54건을 전건 커버, 신 doc_id 중복 0
- ADR 원장 68행, 구 id 가 실제 DEC 68건을 전건 커버, 신 id 충돌 0
- 신 도메인 7종 **전부 사용됨**(빈 도메인 없음)

**분포**
- 계층: spec 19 · research 13 · canon 8 · plan 6 · ops 5 · release 2 · drop 1
- authority: spec 23 · research 13 · record 9 · canon 8
- 도메인(문서+ADR): design 23+36 · governance 6+15 · infra 9+2 · engineering 6+7 ·
  product 6+1 · ai 2+1 · security 1+4

**드러난 것**: `security` 문서가 1건(위험 감사)뿐인데 ADR 은 4건이다 — 결정을 서술하는 문서가 없다.
P4 에서 `vinc.canon.security-model` 신설 검토 대상으로 올렸다.

**번호 보존이 성립한다**: 시리즈 문자가 도메인으로 갈리므로 `D45 → adr-engineering-045` 처럼
번호를 그대로 두고도 충돌이 없다(검증 완료). 사람용 라벨은 `props.label` 에 남는다.

## 다음 세션이 할 일 (갱신)

1. **인벤토리 결손 보완** — Record 16 + Episode 13의 about 엣지. `vinc_graph(depth=1)` 10여 회.
   P7 안전의 전제이고, Record 94 의 keep/rewrite/drop 분류도 이것이 있어야 한다
2. **Record 94 분류** — 원장에 아직 없다. 이것이 채워져야 P7 삭제 목록이 확정된다
3. **P4 재작성** 3분할: (a) canon 8 + governance/security (b) spec 19 (c) research 13 + ops/plan/release 13
   - 층 완결이 분할 기준이다(층이 끝나야 그 층의 상호참조 린트가 닫힌다)
   - 린트: 영어 전용 · 이모지 0 · em-dash 0 · Vinclums 0 · `[[...]]` 전량 신 doc_id · 원장-파일 1:1
4. SCHEME.md §P4 미결 7건 판정

## P4 canon 층 완료 (2026-08-06)

`rewrite/` 9건 · 2014줄 · 125KB · **린트 0 위반**(영어 전용·이모지 0·em-dash 0·Vinclums 0·
상호참조 전량 신 doc_id·프론트매터 6키·status 부재).

### 1→2 분할 하나

`vinc.b.architecture` 의 §2 에 결정 42건 내용이 통째로 있었고 그 결정들은 그래프에 ADR 노드로
이미 존재했다. 불변 원칙 문서가 무관한 결정마다 개정되던 원인이다.
→ `vinc.canon.principles`(안 변하는 것) + `vinc.canon.decisions`(대장) 로 분리.
원장에 반영(54→55행).

### 재작성 중 실측으로 정정한 것 넷

1. **MCP 도구 개수** — 같은 문서가 "19종·읽기 13"과 "18종·읽기 12"를 함께 말했다.
   레지스트리 실측 **19종(읽기 13·쓰기 6)** 으로 확정
2. **`Episode.date`·`summary`** — 스키마 미선언인데 저장은 된다. `mcp-contract` §5.1 에
   결함으로 명시. 최근 Episode 9건 date null 의 원인
3. **`ATTACHES_TO` 정식화가 검증기 변경을 요구한다** — `graph-conventions` §8 규칙 5 가
   "와이어 6종 밖이면 BLOCK" 이라 6→7 을 함께 안 고치면 정식화한 타입이 거부된다.
   §8.1 신설. 데이터 이행은 없다
4. **끊어진 상속** — `inherits_from: weg.c.memory-standalone` 는 코퍼스 밖이라 영원히
   미해소였다. 선언을 버리고 출처는 산문으로(P4 미결 6번 해소)

### 문서가 스스로 말하는 결손 (숨기지 않은 것)

- `security` ADR 4건인데 그 모델을 서술하는 canon 문서가 없다 → `decisions` 에 명시
- 결정 68건 중 날짜를 아는 것이 약 15건 → "날짜를 지어내지 않았다"고 본문에 명시
- 공유 팩의 "임베딩은 수신 측 재생성이 정본"이 오늘 성립하지 않는다(워커가 모델 미적재)
  → `share-pack`·`chunk-ssot` 양쪽에 Known gap 으로

### 다음: spec 19건

분할 기준은 층 완결이다(층이 끝나야 그 층 상호참조 린트가 닫힌다).
canon 9건이 **문체·구조의 본보기**이므로 spec 은 서브에이전트 병렬 가능 —
canon 을 스타일 정본으로 물려주고 `lint.py` 를 게이트로 건다.

## P4 spec 층 완료 + 새로 드러난 결손 (2026-08-06)

`rewrite/` 28건(canon 9 + spec 19) · 6236줄 · **린트 0 위반**.

### 결정 등록부가 절반만 담고 있었다 (신규 발견)

코퍼스가 인용하는 D 계열 결정은 **74건**인데 그래프 노드는 **42건**뿐이다.
**31건이 문서 산문 안에만 있고 노드가 없다**:
```
D1 D2 D3 D5 D8 D10 D20 D21 D22 D23 D24 D25 D27 D28 D29 D30
D33 D34 D39 D40 D41 D49 D50 D51 D52 D58 D62 D64 D65 D66 D67
```
(D48 은 라벨이 `D48-T` 라 목록에 뜨지만 등록돼 있다 → `adr-design-048`)

**어떻게 벌어졌나**: 등록부가 뒤처졌는데 프로젝트는 계속 결정했다. 원본 문서 하나가
"DEC 레지스터가 D44 에서 멈춰 있어 D45 등재는 후속 과제"라고 적어 뒀는데 그 후속이 안 왔다.
이후 결정, 특히 **기존 결정의 개정**은 해당 문서 안에 `⤷ D34 개정` 식 하위 항목으로만 쓰였다.
개정이야말로 지금 유효한 판단인 경우가 많다(첫 답이 틀렸을 때 생기는 것이므로).

**왜 중요한가**: 노드 없는 결정은 다른 노드가 인용할 수 없고, 순회에 안 잡히고,
"이걸 바꾸면 뭐가 딸려 오나"를 그래프에 물을 수 없다. 판단을 보이게 하려고 만든 도구에
프로젝트 설계 판단의 절반이 안 보였다.

**여기서 닫지 않는다**: 산문에서 31개 노드를 만들려면 아무도 다시 안 읽은 결정의 제목·날짜·
범위를 지어내야 한다. 그것이 이 마이그레이션이 막으려는 바로 그 일이다.
`vinc.canon.decisions` 에 결손으로 기록했고, 문서 머리의 "모든 결정의 색인" 주장도 정정했다.

### 서브에이전트가 내 오류를 잡았다

내가 원장 노트·지시문에 "`adr-design-037` 이 `adr-design-077` 로 뒤집혔다"고 적은 것은 **틀렸다**.
D077 은 콘텐츠 평면으로 새어 나간 **노드 유리**를 걷어내 D037 을 **복원**한 것이고 크롬 유리는
명시적으로 존치했다. 은퇴 근거도 취향이 아니라 실측이었다 — 기본 테마(다크)에서 그 유리는
애초에 렌더되지 않았고, 물리 셰이더와 환경맵 두 벌을 유지하며 대다수 화면에 아무것도 안 냈다.
원장 노트와 `canon.decisions` 두 줄 정정 완료.

### spec 재작성이 정정한 실측 (일부)

- `product-overview`: v0.6.7→**v0.7.4** · 도구 18종→**19종** · `vincs.app` 병행 유지는 사실 아님 ·
  데스크탑 로컬 임베딩 탑재 서술은 패키징 실측과 어긋남 · `vinc mcp install` 셸 명령은 존재하지 않음
- `settings`: 문서가 **세 번째로** 형태를 틀리게 적고 있었다(3항목→7항목 평면→실제는 **4탭 모달**).
  v0.2 때 코드 주석과 문서가 서로를 인용하는 순환도 발견됐던 자리다
- `web-system`: 마이그레이션 008→**018** · `starter-packs/` "어떤 리포에도 없다"는 **해소됨**
  (2026-07-31 앱 리포 편입) · 트랙 6 CI 가드는 **여전히 미구현** 확인
- `email-subsystem`: **전역 일일 발송 상한은 한 번도 구현된 적이 없는데** 원본이 그것을 전제로
  쓰여 있었다 · **토큰 `purpose` 3값인데 로그인 소비 경로만 안 본다** → 7일짜리 초대 토큰으로
  세션이 나온다(열린 결함, `vinc.ops.risk-audit-2026-08-05` 가 정본)

### 다음: research 13 + ops 5 + plan 6 + release 2 = 26건

## P4 문서 축 완료 (2026-08-07)

`rewrite/` **54/54** · 12,717줄 · 881KB · **린트 0 위반** · **원장↔파일 1:1**
authority 는 닫힌 4값만: spec 23 · research 13 · canon 9 · record 9

### ⚠ P4 가 아직 안 끝났다 — 노드·엣지 원장이 없다

계획서 P4 항목은 "신 문서 + **기계가독 그래프 원장(노드·엣지 YAML)**" 을 요구한다.
**문서 축만 했고 그래프 축을 빠뜨렸다.** 발주자가 지적해 드러났다.

| 대상 | 상태 |
|---|---|
| 문서 54 | 완료 |
| ADR 노드 68 | 영어 제목 `canon.decisions` 에 있음 |
| 문서 스텁 노드 44 | 영어 제목 문서 프론트매터에 있음 |
| **그 밖의 노드 198** | **완료**(2026-08-07, 아래 "노드 원장 완료" 절) |
| **엣지 467** | 재배선 판정 대기. 끝점 해결은 아래 절에서 458/467 로 실측 정정됨 |

> 아래 "양끝 신 id 를 아는 것은 127개뿐" 은 노드 원장 이전의 수치였다. 원장이 완성되면서
> **458/467** 로 바뀌었다.

노드 제목의 em-dash·이모지는 **0건**(실측 310개). 문제는 한국어이지 기호가 아니다.

**엣지는 판단이 필요하다**: 기계적 재배선(id 치환표)이냐 재설계냐. `relates_to` 가 179개로
38%를 차지하는데 대부분 "관련 있음" 이상을 말하지 않고, 도메인 8→7 재편으로 `cross_domain`
판정도 전부 바뀐다. 발주자 결정 대기.

### 잔여 26건 재작성에서 나온 실측

**risk-audit P0 3종 전부 여전히 유효**(2026-08-07 코드 재확인):
- 토큰 `purpose` 미검증 — `authTokens.ts` 의 `peek`·`consume` 에 purpose 조건 없음.
  대조군 `consumeDelete`·`consumeTeamInvite` 는 걸려 있다
- 검색 청크 팔 owner 미필터 — `_chunk_arms` 에 owner 인자 자체가 없다
- 동기화 apply 무스코프 — `apply_delta` 세 자리 전부 owner 술어 없음

**새 실마리 — 임베딩 워커**: 구운 모델을 쓸 때도 `huggingface_hub` import 가 **무조건** 실행된다.
안 쓰는 의존성의 패키징 결손만으로 전체가 멈출 수 있다. 별건: 모델 레지스트리 불일치
(`store/config.py` 기본값과 워커 해석이 다른 모델. 둘 다 768차라 무음).

**`Vinculums/Vinc` 는 오늘 private 이다** — `adr-infra-044`($0 서명 경로)가 공개 전환을
전제했으므로 그 전제가 성립하지 않는다. 유료·무료 경로 **양쪽 다 미착지**.

**리포 정본의 자기모순 2건**(그래프가 아니라 리포 파일을 고쳐야 함):
- `DEPLOYMENT.md` 체크리스트가 "버전 6곳" 인데 §1.5 는 10곳
- 같은 파일 §4.4 가 "무료 15회/일" 인데 계정 정책은 2,000회

**`GM-6` 는 미등록이 아니라 결번** — 번호를 할당했으나 측정이 안 돼 결정이 되지 못했다.

## 린트가 못 잡던 결함 하나 (2026-08-07)

**프론트매터가 YAML 로 파싱되는지를 안 봤다.** 키 존재만 검사했다. 제목에 콜론이 있는데
따옴표가 없으면 `mapping values are not allowed here` 로 **블록 전체가 깨진다** — 그러면
`doc_id`·`domain`·`authority` 가 전부 안 읽힌다. 3건이 그 상태로 커밋돼 있었고
서브에이전트가 잡았다.

`lint.py` 에 `yaml.safe_load` 검사를 넣었다. 도구가 못 보는 결함은 반드시 다시 생긴다.

## `adr-design-048` 번호 충돌 (미해소)

`dec-d48-t` 의 1회성 접미사 `-t` 를 버려 `adr-design-048` 로 정했는데,
**원장에 없는 다른 `D48`**(레이아웃 프리셋 추가, 미착수)이 문서에 인용된다.
접미사를 버린 결정이 미등록 결정과 번호를 겹치게 만들었다.
`vinc.research.view-redesign` 에 경고로 명시돼 있다. **P5 전에 판정 필요.**

같은 계열로 원장 31건 목록에도 없는 변형 라벨: `D32-a`(recede 값 개정) · `D46-S`(평면 L0 전량) ·
`D45-V`·`D45-V2`·`D45-E`(결정이 아니라 코드 계열 라벨).

## motion 판정 — research 유지 (근거 있음)

v0.7.4 에서 구현된 것은 연구가 권고한 것과 **조직 원리가 다르다**.
연구의 원리는 토큰 파일, 구현의 원리는 **기준점**(눌린 버튼에서 자라고 그리로 빨려 든다).
연구는 스프링 프리셋 표를 권했으나 코드에 stiffness/damping 이 하나도 없고
`linear()` 곡선도 0건이다. Motion 런타임도 미도입(WAAPI 직접 사용 — 이유는 연구가 제기하지
않은 것: **닫기를 기다릴 수 있어야 DOM 을 지운다**).

연구가 그 지점을 못 찾은 이유까지 문서에 적혔다: 부록이 "이미 애니메이션돼 있다"를 확인하고
기여를 수치 토큰화로 결론냈는데, **있는 값을 감사했을 뿐 값이 아예 없는 상태를 묻지 않았다.**
퇴장 애니메이션과 공간 기준점은 상수가 없어 감사에 안 잡혔다.
승격했다면 코퍼스가 없는 스프링 모델을 as-built 로 주장하게 된다.

## 규칙의 발원지가 규칙을 어기고 있다

데스크탑 **창 제목과 번들 설명에 em-dash 가 들어 있다**(실측). em-dash 금지를 낳은 문서가
서술하는 바로 그 표면이다. 코드 수정이라 이번 범위 밖 — 별건으로 남긴다.

## 상속 복원 (2026-08-07, 발주자 결정)

구 체계는 문서 프론트매터의 `inherits_from` 을 엣지로 미러했다(75개 `DOC→DOC`).
신 문서가 그 선언을 0건 했으므로 원천이 사라진 상태였다. **명시 복원**을 택했다.

산출: `inherits_map.tsv`(54행) · `build_inherits.py`(구→신 이관) · `check_inherits.py`(순환·역전 검사)

**의미는 계층 사슬이 아니다.** `vinc.a.terms` 7회, `vinc.d.index` 9회,
`vinc.d.atlas-visual-language` 10회 피상속이 말해 주듯 **"이 문서의 사실이 저 문서에 기댄다"**
는 실제 의존이다. 그 성격을 유지했다.

- 기계 이관 **38건** · 판단으로 채운 것 **16건**(구 선언 없던 13 + 코퍼스 밖만 가리키던 2 + 신설 1)
- 코퍼스 밖(`weg.c.memory-standalone`)은 버렸다 — 영원히 해소 불가
- 분할된 `vinc.b.architecture` 를 기댄 쪽은 **불변 원칙**에 기댄 것이므로 `canon.principles` 로

### 기계 변환이 계층을 10곳 뒤집었다

구 코퍼스는 canon/spec 구분이 없어 "C 가 C 를 상속"이 자연스러웠다. 갈라 놓으니 방향이 뒤집혔다.

| 종류 | 건수 | 처리 |
|---|---|---|
| **canon → spec** | 6 | **끊음.** 계약이 구현에 기대면 계약이 아니다 |
| **spec → research** | 4 | **`derives_from` 으로 이동.** 이건 의존이 아니라 **출처**다 |

`derives_from` 은 엣지 어휘에 이미 있고(35개 운용 중) 뜻이 정확하다. 그 덕에 `inherits_from` 은
**항상 계층 방향(위→아래)만** 가리키게 되어 린트로 강제할 수 있다.

결과: **순환 0 · 역전 0 · 엣지 92개.** 린트에 세 검사 추가(대상 존재·계층 역전·derives 대상 존재).

피상속 허브: `spec.atlas-visual-language` 12 · `canon.terminology` 11 · `spec.surface-tree` 10.
이 셋이 바뀌면 파급이 가장 크다.

## 노드 원장 완료 (2026-08-07)

`ledger-nodes.tsv` **198/198**. concept 66 · bridge 6 · episode 48 · **record 78(이번 회차)**.
빈 자리는 설계상 비는 것뿐이다 — bridge 6 은 구조 노드라 도메인이 없고, drop 6 은 도메인이 무의미하다.

| disposition | 건수 | 뜻 |
|---|---|---|
| keep | 182 | 사실이 그대로 선다. 영어 제목·note 만 새로 쓴다 |
| rewrite | 10 | 내용 자체가 낡았거나 재설계가 해소했다 |
| drop | 6 | 옮길 값이 없다 |

### 원문은 리포가 아니라 그래프에 있었다

record 78 건의 실체(`props.note`)는 `inventory/nodes.tsv` 에 없다 — 인벤토리는 id·제목·도메인·
tags·extra 만 떴다. 계정 그래프에서 78 건을 끌어와 썼다. 다음에 노드 내용이 필요하면
인벤토리를 보지 말고 그래프를 봐야 한다.

⚠ **허브에 `target` 을 실어 보내면 안 된다.** `vinc_graph` 인자에 `target:"remote"` 를 넣으면
허브가 그것을 **자기 원격 호출로 해석**해 "requires a key" 로 튕긴다. 클라이언트 쪽 인자가
서버로 새는 것이고, `remote.py` 는 `owner` 만 걸러 낸다. 진단이 어려운 이유는 실패가 401 이
아니라 정상 200 안의 error 필드로 오기 때문이다.

### 재설계가 이미 해소한 record 5 건 (rewrite)

| id | 무엇이 바뀌었나 |
|---|---|
| `fact-quota-limits` | 무료 15/일이 정본에서 폐지됐다(2026-08-02, 2000/일 + 주제 5개) |
| `fact-f1-gate-drift` | 정본↔게이트 불일치를 2026-08-02 개정이 닫았다. 재작성본 §4 에 반영돼 있다 |
| `fact-atlas-layout-presets` | 이 record 가 경고하던 "난 서술 3곳"이 재작성으로 사라졌다(grep 0건) |
| `dec-numbering-fork` | P3 이 `adr-{domain}-{NNN}` 으로 갈래를 합쳤다 |
| `node-type-distribution-3` | **실측이 어긋났다.** "Bridge·Entity 0" 이었는데 bridge 는 2026-08-01 부터 6개 실재한다(Entity 는 여전히 0). 마크 판정 당시 데이터의 사실로 시점을 박아 두었다 |

`fact-0-7-2-unknown-streams` 는 drop 이다. `vinc.next-session` 과 같은 종(세션 핸드오프)이고
"발주자가 있다고 말했는데 목록을 모른다"는 무지의 기록이라 옮길 사실이 없다.

### `vinc/web` 해체 9건 — 적용한 규칙

SCHEME 의 문장(정책은 product · 구현은 engineering · 운영은 infra)을 **사실의 성격**에
그대로 걸었다. 코드가 어디 사는지가 아니라 그 사실이 무엇에 관한 것인지로 가른다.

- **engineering 5** — `allowlist-check`(진단 수단) · `ap1-webhook-path`(라우트) ·
  `risk-csrf-not-slid` · `risk-sweep-expired-uncalled` · `risk-team-plan-bleeds`(전부 코드 결함)
- **infra 3** — `dns-6` · `resend-4`(DNS) · `newsletter-2`(시크릿 정합)
- **product 1** — `psp-korea`(한국 판매자가 쓸 수 있는 수납 경로. 정책 입력이다)

⚠ `risk-team-plan-bleeds` 는 판정이 갈릴 수 있다. AP-1 등급 정책의 급소라 product 로 볼 여지가
있으나, record 본문이 `billing/service.ts:29`·`subscriptions.ts:62` 두 코드 결함이라 engineering 으로 뒀다.

### `lint_nodes.py` 신설

문서 축에서 배운 것이 노드 축에 없었다 — `lint.py` 는 `ledger-documents.tsv` 와 재작성본만 본다.
노드 원장은 **아무도 안 보고 있었다**. 7가지를 검사한다: 열 형상 · kind/disposition 값공간 ·
new_domain 7종(빈 값은 bridge·drop 만) · title·note 필수 · em-dash/en-dash/이모지 금지 ·
**note 에 한글 잔존**(번역 누락) · title 유일 · attach 대상 해결 · 인벤토리 id 1:1.

검사기 자체를 결함 7종을 심어 검증했다(전부 잡고 exit 1). **통과하는 린트와 빈 린트는
출력이 같다** — 그것이 이 프로젝트에서 이미 한 번 물린 자리다.

현재 판정: **198행 위반 0.**

### 남은 것 — 엣지 467

노드 축은 닫혔다. P4 에 남은 것은 엣지뿐이다.

**"340개는 목적지 미정" 은 더 이상 참이 아니다.** 세 원장을 합쳐 실측하면
**467 중 458 이 양끝 신 id 를 안다.** 남는 9 는 전부 버리기로 이미 정해진 끝점이다
(`doc.vinc.next-session` drop 4 · 코퍼스 밖 `weg.c.memory-standalone` 4 · drop 노드 1).

즉 **기계적 재배선의 걸림돌은 사라졌다.** 남은 것은 순수한 설계 판정이고 그것만
발주자 결정 대기다:

| 타입 | 건수 | 재판정 사유 |
|---|---|---|
| `relates_to` | 179 (38%) | 대부분 "관련 있음" 이상을 말하지 않는다. 이 38% 를 그대로 옮기면 재설계가 아니다 |
| `attaches_to` | 88 | SCHEME 이 정식화했다. 그대로 |
| `inherits_from` | 75 | 상속 복원에서 이미 92개로 재작성됐다. **이 75 는 폐기하고 신본을 쓴다** |
| `about` | 59 | Episode→Concept. 그대로 |
| `derives_from` | 35 | 그대로 |
| `connects_via` | 22 | Bridge→Concept. 그대로 |
| `prerequisite_of` | 9 | 그대로 |

도메인 8→7 재편으로 `cross_domain` 판정은 전 엣지에서 다시 계산해야 한다(기계).

### 엣지 재설계는 판단 대상이 223개다

"467개 재설계"는 다루기 어려운 크기인데 실제로는 네 무리다:

| 무리 | 수 | 성격 |
|---|---|---|
| 파생 | 75 | `inherits_from` — **위에서 복원 완료**(92개로 재생성) |
| 구조 | 147 | `attaches_to` 88 + `about` 59. 노드가 정해지면 따라온다 |
| 브리지 | 22 | `connects_via`. 물리화 유지 여부가 별도 결정 |
| **판단** | **223** | `relates_to` 179 + `derives_from` 35 + `prerequisite_of` 9 |

`relates_to` 179 의 구성: 개념↔개념 55 · 한쪽이 ADR 76 · **한쪽이 문서 스텁 66**.
마지막 66개가 의심스럽다 — 문서와 개념의 연결은 이미 `Chunk-MENTIONS-Concept` 로 파생되므로
그 위에 `relates_to` 를 또 그으면 같은 사실이 두 표현으로 존재한다.

엣지 차수 허브: `concept:atlas` 27 · `account-backend` 20 · `graph-md` 19 · `hub` 19.

---

## P5 재실행 완료 (2026-08-09)

이 문서는 2026-08-08 에 "P5 컷오버 완료"로 닫혔다. **그 상태는 그날 밤 사라졌다** —
재생 불가 WAL 로 그래프가 되감겼고 P5 결과가 전부 없어졌다. 살아남은 것은 이
디렉터리의 원장·재작성본·스크립트뿐이었고, 그것으로 결정론적으로 재현했다.

> 교훈은 절차가 아니라 판정에 있다. "끝났다"를 쓰기 전에 라이브를 조회할 것.
> `vinc_domains` 한 번이면 갈린다. 파일이 우기는 것과 그래프가 말하는 것은 다르다.

### 실행 결과

| 단계 | 결과 |
|---|---|
| 노드 재작성 | **created 0 · matched 192** — 전부 제자리. id 어긋남 0 |
| drop | 6/6 (`found=True`) |
| 문서 적재 | 54/54 |
| 구 문서 삭제 | **49/50** — 1건이 FTS 불일치로 실패(아래) |
| 학습자료 퍼지 | 53/53 |
| 도메인 재매핑 | 자동 26 + 판정 3 = 29. **구 도메인 0** |
| doc-concept 정리 | 41/41 |
| 계보 적재 | 스텁 54(created 51 · matched 3) · **엣지 96/96** |

최종 도메인: design 77 · engineering 68 · infra 62 · security 38 · governance 37 ·
product 27 · ai 5. 신 7종만 남았다.

### 스크립트에서 잡은 결함 둘

**`olddocs` 가 방금 올린 재작성본을 지우고 있었다.** 모듈 독스트링은 "신 문서 id 는
구 id 와 전부 다르다"고 적었는데 4건에서 거짓이다 — `vinc.ops.deployment` ·
`vinc.ops.incident-2026-08-04-easyauth` · `vinc.ops.risk-audit-2026-08-05` ·
`vinc.plan.code-signing` 은 주소가 이미 신 체계에 맞아 id 를 그대로 뒀다. 그래서
`docs` 가 제자리 재작성한 것을 `olddocs` 가 같은 id 로 지운다. 오류 없이 총계만
4 줄어든 채로. 2026-08-08 의 잃어버린 실행에서도 이미 그랬을 것이다.

**`-` 표식이 삭제 대상에 섞여 있었다.** 원장에서 "구본 없는 신규 문서"를 뜻하는데
`if r.get("old_doc_id")` 로는 안 걸러져 `vinc_doc_delete("-")` 를 부르고 있었다.

### 도중에 터진 것 — FTS 색인 불일치

구 문서 하나(`vinc.d.atlas-mark-verdict`) 삭제가 엔진 오류로 실패했다:

    FTS index 'chunk_ft' is inconsistent: document for node offset 992 is
    missing during delete. Drop and recreate the FTS index.

세 번째다. 조사·재현·처방은 그래프의 `vinc.ops.fts-index-drift` 에 있다. 요지만:
드롭 후 재생성으로 5.8초에 낫고, 그날 쓰기 경로에 자기복구를 달아 라이브에서
동작을 확인했다(같은 삭제가 청크 9건 제거로 성공). 기각된 가설 셋도 그 문서에 있다.

### 판정 3건 (원장 밖, 2026-08-06 이후 생성이라 원장에 없음)

`vinc/web` 은 원장에서 갈려(product 7 · engineering 7 · infra 4) 자동 매핑에서
빠졌는데, 남은 3건은 각각 내용으로 명확했다:

- `record:fact-starter-seed` → `vinc/infra`
- `record:risk-starter-seed-manual` → `vinc/infra`
- `record:risk-community-download-open` → `vinc/security`

네 번째였던 `concept:doc.vinc.c.web-vincs-app` 은 구 문서가 지워져 doc-concept
정리에서 함께 사라졌다.

### 남은 것

- **엣지 재배선 판정 263건**(2026-08-09 센서스) — 발주자 결정 대기.
  ⚠ 이 자리에 **223** 이라고 적혀 있었는데 그건 08-06 인벤토리 수치였다. 바로 위
  「엣지 판단 대상 재측정」절이 08-08 에 **160** 으로 정정했는데도, 08-09 재실행
  기록을 쓰면서 낡은 수를 그대로 다시 적었다. 같은 파일 안에서 정정이 회귀한 것이다.
  세 수치가 동시에 유통되는 상태를 끝내려고 계보를 여기 못박는다:

  | 시점 | 판단 대상 | 무슨 일이 있었나 |
  |---|---|---|
  | 2026-08-06 | 223 | 최초 인벤토리 |
  | 2026-08-08 | 160 | P5 가 문서·doc-concept·학습자료를 지워 판단 표면이 줄었다 |
  | **2026-08-09** | **263** | P5 재실행이 **계보 엣지를 새로 깔았다**(`INHERITS_FROM` 92) |

  즉 160 → 263 은 판정이 밀린 게 아니라 **판단 대상이 새로 생긴 것**이다. 구성은
  `RELATED_TO` 139 · `INHERITS_FROM` 92 · `DERIVES_FROM` 23 · `PREREQUISITE_OF` 9,
  전체 엣지 500 기준(원장 `edge-census.json`).

  ⚠ **263 은 08-09 측정이고 오늘 다시 재지 않았다.** 이 수를 근거로 무언가를
  끝내려면 `edge_census.py` 를 먼저 돌릴 것 — 이 파일이 앞에서 세 번 배운 그대로다.
  그리고 위 실행 결과표는 상속 엣지를 **96/96** 이라고 적는데 센서스는 92 다.
  4건 차이의 정체(중복 접힘인지 방향인지)는 아직 안 봤다.
- **제목이 한국어인 노드** — 원장 밖에서 만들어진 것들은 재작성 대상이 아니었다.
  신 체계는 영어이므로 언젠가 정리 대상이다. 규모는 아직 안 셌다
- 리포 삭제는 **위 둘을 기다릴 이유가 없다.** 지켜야 하는 것은 `_migration/`
  하나뿐이고, 그것은 2026-08-11 에 `Vinc/_migration/` 으로 복사해 두었다(원본은
  발주자가 리포를 지울 때까지 여기 남긴다 — 옮기는 대신 복사한 것은 이관 중
  유실 가능성을 0 으로 두기 위해서다). 나머지 마크다운 스냅샷은 이미 그래프에 있다
