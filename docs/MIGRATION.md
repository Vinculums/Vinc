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
python -m engines.recall_to_neo4j                        # :Episode (owner·domain)
python -m engines.concepts_to_neo4j                      # :Concept (도메인 자동 발견)
python -m engines.bridges_to_neo4j                       # :Bridge (source=MEMORY_BRIDGE_SOURCE)
cd -                                                     # memory 레포로 복귀
python -m engines.learner_to_neo4j \
    --source $MEMORY_HUB_ROOT/CONTEXT/identity/user-model.md --owner $MEMORY_DEFAULT_OWNER
# (선택) embed_pipeline / skill_tracker / graph_to_neo4j 도 동일하게 재실행
```

## 경로 B — in-place 백필 (재수집 불가 시)

원문 없이 DB 만 있을 때. 단, v1 이 적재한 `:Episode` 는 `domain` 이 없고 `workspace`
도 비어 있는 경우가 많아 **커버리지가 낮게 남는다** — 가능하면 경로 A.

```bash
python -m store.provision --apply                                        # 새 스키마
python -m store.provision --backfill-owner --backfill-domain --apply     # owner ← 기본값, domain ← workspace
python -m engines.learner_to_neo4j --source .../user-model.md --owner user:doheon
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
