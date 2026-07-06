# v1 스토어 마이그레이션 — 기존 weg-9000 메모리 데이터 이전

> 대상: v1(`weg-9000/weg9000-core` 스크립트)로 **로컬 Neo4j 에 이미 적재한** 메모리
> 데이터를, 개인 학습 모델(owner_id · 열린 domain · LearnerPattern)로 끌어올린다.
> 모든 단계는 멱등·비파괴 — 기존 노드를 지우지 않는다.

## 무엇이 달라졌나 (v1 스토어에 없는 것)

| 항목 | v1 스토어 | 현재 모델 |
|---|---|---|
| 소유 | 없음 | `owner_id`(개인 1차) + `tenant_id`(거버넌스) |
| 도메인 | `:Concept` 만 보유 | `:Episode`/`:Concept` 모두, **자유 문자열**(목록 강제 없음) |
| 학습법 | 없음 | `:LearnerPattern`(user-model U-패턴) |
| 스키마 | 제약 10 + 인덱스 2 | 제약 11 + 인덱스 8 = **19 statements** |

## 사전 준비 (로컬 서버)

```bash
cd weg-9000-memory
export NEO4J_URI=neo4j://127.0.0.1:7687 NEO4J_USER=neo4j NEO4J_PASSWORD=...
export MEMORY_HUB_ROOT=/path/to/weg-9000/weg9000-hub   # v1 허브 체크아웃
export MEMORY_DEFAULT_OWNER=user:doheon                  # 학습자 owner_id
```

## 경로 A — 재수집 (권장)

허브 원문(세션 로그·개념 노트·user-model)이 그대로 있으므로, **새 엔진으로 다시
적재**하는 것이 가장 깨끗하다. MERGE 키(episode_id/concept_id)가 같아 기존 노드는
덮어써지며(속성 보강), owner/domain 이 원문 frontmatter 에서 제대로 붙는다.
(실 세션은 `domain: art` 를 이미 갖고 있고, `domain: [math, art]` 리스트형은 첫
항목을 주 도메인으로 정규화한다.)

```bash
python -m store.provision --apply                        # 스키마 19 statements (멱등)
cd $MEMORY_HUB_ROOT                                      # recall/graph 는 CWD 기준
python -m engines.recall_ingest                        # :Episode (owner·domain)
python -m engines.concepts_ingest                      # :Concept (도메인 자동 발견)
python -m engines.bridges_ingest                       # :Bridge (source=MEMORY_BRIDGE_SOURCE)
cd -                                                     # memory 레포로 복귀
python -m engines.learner_ingest \
    --source $MEMORY_HUB_ROOT/CONTEXT/identity/user-model.md --owner $MEMORY_DEFAULT_OWNER
# (선택) embed_pipeline / skill_tracker / graph_to_neo4j 도 동일하게 재실행
```

## 경로 B — in-place 백필 (재수집 불가 시)

원문 없이 DB 만 있을 때. 단, v1 이 적재한 `:Episode` 는 `domain` 이 없고 `workspace`
도 비어 있는 경우가 많아 **커버리지가 낮게 남는다** — 가능하면 경로 A.

```bash
python -m store.provision --apply                                        # 새 스키마
python -m store.provision --backfill-owner --backfill-domain --apply     # owner ← 기본값, domain ← workspace
python -m engines.learner_ingest --source .../user-model.md --owner user:doheon
```

## 검증 — "실제로 동작하는가"

```bash
python verify.py                          # 무 DB 게이트 (스키마·플랜·파서)
python e2e_live.py --owner user:doheon    # 라이브 e2e: 스키마·커버리지·recall/domains/graph/learner/activate
python -m api.cli domains --owner user:doheon      # 배운 도메인이 데이터에서 열거되는가
python -m api.cli graph   --owner user:doheon | head -40
python -m api.cli activate --owner user:doheon --out ./pack && cat pack/pack.md
```

`e2e_live.py` 가 PASS 면: 스키마 19 statements 존재, 학습 노드 owner/domain 커버리지
≥90%, recall·domains·graph·learner 가 실 rows 반환, Activate 팩에 에피소드+학습법 포함.

## 알려진 정규화

- `domain: [math, art]` (리스트형) → 첫 항목이 `Episode.domain`. 다중 도메인 표기는
  후속(v2 Connections)에서 관계로 승격 예정.
- owner 없는 기존 노드 → `tenant:shared` (개인 귀속은 `--owner` 로 지정).

## 타입 어휘 마이그레이션 — 전역 enum → 주제별 사용자 저작 (D11-R2 §7)

노드/엣지 `type` 이 **전역 고정 enum** 에서 **주제(theme)별 사용자 저작 어휘**로 바뀐다
(THESIS T3). 구 enum 은 폐기되지 않고 **기본 초안(system_draft)** 으로 지위만 강등된다.
이 마이그레이션은 **`themes.json`(어휘 메타)만 기록하고 그래프는 건드리지 않는다**
— 어휘는 해석의 *틀*이지 *내용*(노드·엣지)이 아니기 때문(IMPL §3).

```bash
python -m store.migrate_themes --dry-run     # 계획만(파일·그래프 무변경)
python -m store.migrate_themes --apply       # legacy 주제 시드 + 실재 타입 재가
```

동작(멱등):
1. `legacy` 주제 생성 → 구 전역 enum 을 `system_draft/proposed` 초안으로 시드.
2. 그래프에 실재 인스턴스(≥1)가 있는 레거시 타입만 `authored` 로 재가(기존 적재분
   이관). 미적재 enum 은 proposed 잔류.
3. `legacy` 를 활성 주제로 지정 → 이후 ingest 가 유효 대상 주제를 갖는다.

이후 신규 적재는 `vinc_ingest(fragment, theme_id)` 로 **theme_id 필수**다. 어휘 밖
type 은 스킵되지 않고 `proposed` 로 편입되어 응답 `proposed_types` 에 담긴다 — 사용자가
`vinc_types_ratify` 로 재가한다(팩 내보내기엔 authored 만 실린다).

> **알려진 갭 — 레거시 CLI 뒷문:** `engines/concepts_ingest.py`·`bridges_ingest.py`·
> `pack_ingest.py` 는 raw Cypher 로 그래프에 직접 쓴다(`fragment_ingest` / 어휘 검증을
> 우회). 즉 **재가되지 않은 타입이 그래프에 들어올 수 있다.** 현재는 사용자가 직접
> CLI 를 돌리므로(=사용자가 저자) 허용 가능하지만, 이 경로가 자동화되면 theme 어휘
> 경유로 재검토해야 한다. 이번 이터레이션 범위 밖(의도적).
