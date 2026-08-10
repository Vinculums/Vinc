# GRAPH.md 설계·문서화 작업 계획 (2026-07-31)

입력: `PROMPT-graph-md.md`(발주) + 외부 연구 보고서 `GRAPH.MD 설계.md`(수령).
이 계획은 연구 보고서를 **코드로 대조해 판정**하고, 규격·배치·검증·문서화까지 실행 단위로 쪼갠다.

근거 라벨: `코드근거(file:line)` · `출처확인` · `추정` · `판단`.

---

## 0. 결론 요약

- **연구 보고서의 뼈대 3개는 채택한다**: (1) 짧은 산문 + 생성 블록의 하이브리드, (2) 정본은
  스토어이고 GRAPH.md 는 파생 뷰, (3) 범용 코어 분리는 보류하고 섹션 태그로만 표시.
- **그러나 배치 권고는 뒤집는다.** 연구는 (a)리포 파일 → (b)그래프 문서 → (c)MCP resource 를
  권했지만, **(c)는 지금 우리 서버에 아예 없고**(`capabilities: {"tools": {}}` 코드근거
  `Vinc/vinc_mcp/rpc.py:114`) resources 는 애플리케이션 제어라 모델이 스스로 못 부른다.
  대신 **(d) 읽기 전용 MCP 도구 `vinc_conventions` + `vinc_ingest` 설명문 한 문장**이
  모든 호스트에서 사람 개입 없이 동작하는 유일한 경로다. 도구 목록은 모델 제어이고 항상
  컨텍스트에 실린다. 비용은 tools.py 약 20줄.
- **연구가 놓친 코드 사실 하나가 템플릿 문구를 바꾼다**: `content_time` 은 HTTP `/doc/put` 은
  받지만(코드근거 `Vinc/api/server.py:488`) **MCP `vinc_doc_put` 이 전달하지 않는다**
  (코드근거 `Vinc/vinc_mcp/tools.py:190`). 어시스턴트에게 "채우라"고 적으면 그 줄은
  **오늘 실행 불가능한 지시**다. 읽기는 되고 쓰기만 안 된다(§1.2-6). 노출·미노출·제안재가 3안은
  판단이 아니라 **실측으로 가른다**(§8.1, 코드 0). GM-1 의 블로커가 아니다.
- **DOCS 는 새 섹션을 만들지 않는다.** `/docs/mcp/graph-md/` 한 쪽을 MCP 섹션 3번째 페이지로
  넣고, `/docs/`·`/docs/use-cases/`·`/docs/mcp/` 세 곳에 포인터만 건다. Stitch 형 독립 섹션은
  GM-1 이 실측으로 통과한 뒤에 검토한다.
- **하지 말아야 할 것도 있다.** 스토어 문서로 넣는 순간 **계정↔로컬 동기화가 두 그래프의
  관례 문서를 서로 덮어쓴다**(코드근거 `Vinc/api/docsync.py:32` 전량 열거, 제외 필터 없음).
  이 제외 처리 없이 (b)를 켜면 안 된다.

---

## 1. 연구 보고서 판정

### 1.1 채택·수정·기각

| # | 연구 보고서 주장 | 판정 | 근거 |
|---|---|---|---|
| R1 | 하이브리드(짧은 산문 + 생성 블록) | **채택** | 스토어에 재가 어휘가 이미 있음(`api/server.py:625` read_types) |
| R2 | 정본 = 스토어, 문서는 파생 뷰 | **채택** | 둘 다 정본이면 어긋난다는 판단이 옳다 |
| R3 | 60줄 상한 | **채택**(하한선 추가) | 60줄 경고 / 100줄 실패로 게이트화(§5) |
| R4 | 입도는 규칙 아닌 3~5 예시 | **채택** | 다만 예시를 도메인 중립으로 교체(§2.3) |
| R5 | 범용 코어 분리 보류, 섹션 태그만 | **채택** | 구현체 1개 |
| R6 | 배치 (a)→(b)→(c), (c)는 Claude Code 한정 실험 | **수정** | (c) 미구현·모델 미제어. (d) 도구화로 대체(§3) |
| R7 | `content_time` 을 어시스턴트가 채운다 | **기각** | MCP 경로에 필드 없음 `vinc_mcp/tools.py:190` |
| R8 | 자동 생성은 골격까지 | **채택** | 단 "골격"의 경계를 §2.5 로 명시 |
| R9 | 도메인 예시 research/product/personal | **보류** | 보고서 자신이 가정이라 명시. 생성 블록이 채운다 |
| R10 | 지표 = proposed_types 발생률 | **수정** | 발생률은 오늘 못 잰다(텔레메트리 없음). 재고(stock) 지표로 대체(§8) |

### 1.2 코드 대조로 새로 드러난 것 (연구 보고서에 없음)

1. **어시스턴트는 재가된 어휘를 볼 방법이 없다.** MCP 도구 18종에 어휘 조회가 없다
   (코드근거 `Vinc/vinc_mcp/tools.py:203` `TOOLS` 전량 — `vinc_search`·`vinc_recall`·`vinc_graph`·
   `vinc_decide`·`vinc_domains`·`vinc_suggest_ties`·`vinc_neighbors`·`vinc_path`·
   `vinc_merge_candidates`·`vinc_diagnose`·`vinc_ingest`·`vinc_delete`·`vinc_delete_edge`·
   `vinc_merge_nodes`·`vinc_doc_put`·`vinc_doc_get`·`vinc_doc_list`·`vinc_doc_delete`).
   `read_themes`/`read_types` 는 HTTP 전용(`api/server.py:621`·`:625`).
   ⇒ **GRAPH.md 는 "있으면 좋은 관례집"이 아니라 지금 유일하게 비어 있는 채널이다.**
   이것이 GM-1 을 지지하는 가장 강한 코드 근거다.
2. **`content_time` 은 MCP 로 못 쓴다**(§0). 앱·HTTP 로만 채운다.
3. **임의 doc_id 로 문서 생성이 가능하다** — `doc_id = doc_id or make_doc_id(...)`
   (코드근거 `Vinc/api/docstore.py:252`) 이후 `MERGE (d:_DocMeta {doc_id: $id})`(`:312`).
   ⇒ 고정 id `graph.md` 배치가 성립한다.
4. **그 문서는 자동으로 동기화된다** — `export_docs` 가 owner 의 문서를 조건 없이 전량 실어
   보낸다(코드근거 `Vinc/api/docsync.py:32`). 로컬 그래프의 관례가 계정 그래프를 덮어쓴다.
5. **그 문서는 검색에 걸린다** — `put_doc` 이 청킹해 `Chunk` 로 넣으므로
   (`api/docstore.py:265` 이후) `vinc_search` 결과에 관례 문서 조각이 섞인다.
6. **`content_time` 은 읽을 수는 있다.** `vinc_doc_list` 가 문서마다 `content_time` 을 돌려준다
   (코드근거 `Vinc/api/docstore.py:529` 열거 + `Vinc/api/server.py:474` 통과).
   ⇒ 비대칭은 "모른다"가 아니라 **"읽을 수 있는데 쓸 수만 없다"** 이다. 두 가지가 따라온다:
   (i) 템플릿에 "문서의 `content_time` 을 시간 맥락으로 읽어라"는 줄은 **오늘도 유효하다**
   (추가 여부는 60줄 예산 문제라 GM-6 과 함께 결정), (ii) §8 의 GM-6 실측에서 **측정하는
   어시스턴트가 이 도구를 부르면 정답을 먼저 본다**. 블라인드 조건에 이 금지가 들어가야 한다.
7. **MCP 서버는 resources·prompts 를 선언하지 않는다** — `capabilities: {"tools": {}}`
   (`vinc_mcp/rpc.py:114`). 게다가 `initialize` 결과의 선택 필드 추가는 **실사고 이력이 있다**
   (2026-07-31, 협상 안 된 리비전 필드로 claude.ai 커넥터 연결 실패 — 동 파일 주석).
   ⇒ 프로토콜 표면을 새로 여는 선택지는 위험 프리미엄이 붙는다.

---

## 2. GRAPH.md 규격

### 2.1 형태 원칙 (5개, 전부 게이트로 강제 가능)

1. **한 파일·두 구역.** 사람이 쓰는 산문 구역 + 스토어가 쓰는 생성 구역. 경계는 주석 마커.
2. **60줄.** 초과 시 경고, 100줄 초과 시 실패. 늘리고 싶으면 절을 지워야 한다.
3. **층 태그만.** `[core]`(범용) / `[vinc]`(바인딩). 파일 분리는 하지 않는다.
4. **명령형·2인칭.** 독자는 어시스턴트다. "권장한다"가 아니라 "하라/하지 마라".
5. **화면 노출 규칙 준수.** em-dash 금지(D38), 대체는 `·`·괄호·콜론. 이 템플릿은 문서
   사이트에 그대로 실리므로 내부 문서 규칙이 아니라 **제품 카피 규칙**을 따른다.

### 2.2 절 구성

| 절 | 구역 | 담는 것 | 없으면 망가지는 것 | 기계검증 |
|---|---|---|---|---|
| 헤더 | 생성 | 규격 버전·생성 시각·정본 선언 | 드리프트 감지 기준점 소실 | 예 |
| What this is | 사람 | 이 파일의 지위·의미 판단 주체 | 어시스턴트가 이 파일을 스펙으로 오인 | 아니오 |
| Read before you write | 사람 | 검색 먼저·id 재사용·id 접두 규약 | 중복 개념 양산 | 부분(id 접두 형식) |
| Node kinds | 사람 | 5종 정의 + 개방형 타입의 운명 | 같은 자료가 호스트마다 다른 라벨 | 부분 |
| Granularity | 사람 | 양 2 · 음 3 예시 + 2문장 테스트 | 개념 비대·파편화 | 아니오 |
| Relations | 사람 | wire 6종·방향의 의미·relates_to 무방향 | 방향 역전으로 선행 그래프 붕괴 | 예(어휘 대조) |
| Documents | 사람 | `vinc_doc_put` 경로 고정, Record 복붙 금지 | D54 형 "출처 없음" 재발 | 예(문구 존재) |
| Domains in this graph | 생성 | 실 도메인 목록 + 표기 규칙 | 도메인 분열 | 예 |
| Ratified vocabulary | 생성 | 재가된 node/edge 타입 | 제안만 쌓임 | 예 |
| Do not | 사람 | 금지 3~4줄(자기재가 금지 포함) | 재가 비대칭 훼손 | 부분 |

절은 10개지만 생성 구역 2개는 사람이 쓰지 않는다. **사람이 손대는 것은 7절, 약 35줄이다.**

### 2.3 템플릿 (EN · 정본, **실측 58줄** · 공백 포함, 렌더 계측 2026-08-01)

```markdown
# GRAPH.md
<!-- vinc-graph-md: 1 · built 2026-07-31 · the store is canonical, this file is a view of it -->

## What this is [core]
The house style for writing into this knowledge graph. Read it before your first write
in a session. Judging meaning is your job. This file only fixes the conventions.

## Read before you write [core]
Search first (vinc_search), reuse the ids you get back, and prefer attaching to an
existing concept over creating a near duplicate. Ids carry their type as a prefix:
concept:optimistic-locking, episode:2026-07-31-review, record:pricing-note.

## Node kinds [vinc]
- Concept: one reusable idea. If it will not be reused, do not create it.
- Episode: something that happened (a session, an event). It is tied to a time.
- Record: a short note or property hanging off a concept.
- Bridge: a junction that ties domains together.
- Entity: anything you author that is none of the four above.
Any other type still saves, as an Entity, and enters the vocabulary as proposed.
If you cannot decide, use Concept and say so in your reply.

## How big is one concept [core]
GOOD "Optimistic concurrency control": reusable, self standing, one idea.
GOOD "Sunk cost fallacy": same shape, different field.
BAD "Database stuff": several ideas in a bag. Split it.
BAD "What I read today": not a concept. That is an Episode.
BAD "PostgreSQL" and "Postgres" as two nodes: the same thing. Keep one.
Test: if you cannot finish the sentence "X is ..." in two sentences, it is too big.

## Relations [vinc]
- relates_to: loose association. Undirected (the reverse is folded into the same edge).
- prerequisite_of: A comes before B. The direction carries the meaning.
- derives_from: A comes out of B. The direction carries the meaning.
- inherits_from: A inherits from B. The direction carries the meaning.
- connects_via: Bridge to Concept.
- about: Episode to Concept.
Not sure: use relates_to. Never flip a directed edge to make it fit.

## Documents [vinc]
Source text goes in through vinc_doc_put and the store keeps the canonical copy.
Do not paste a whole document into a Record. Link concepts through the document,
not through a copy of it.

<!-- vinc:generated start · rebuilt from the store · do not hand edit -->
## Domains in this graph
research (142) · product (88) · personal (31)
Lowercase english. Do not invent a domain inline: propose it and say why.

## Ratified vocabulary
node types: none beyond the five above
edge types: cites, contradicts
Anything else you write is kept and marked proposed, for a person to ratify.
<!-- vinc:generated end -->

## Do not [vinc]
- Do not ratify your own proposal. A person does that.
- Do not create a node for something you did not search for first.
- Do not invent a domain name inline.
```

### 2.4 템플릿 (ko · 1:1 이식)

```markdown
# GRAPH.md
<!-- vinc-graph-md: 1 · built 2026-07-31 · 정본은 스토어이고 이 파일은 그 사본입니다 -->

## 이 파일은 무엇인가 [core]
이 지식그래프에 쓸 때의 관례입니다. 세션에서 첫 쓰기를 하기 전에 읽으십시오.
의미를 판단하는 것은 당신의 일입니다. 이 파일은 관례만 고정합니다.

## 쓰기 전에 읽을 것 [core]
먼저 검색하고(vinc_search), 돌려받은 id 를 재사용하고, 비슷한 개념을 새로 만들기보다
기존 개념에 붙이십시오. id 는 종류를 접두사로 답니다:
concept:optimistic-locking, episode:2026-07-31-review, record:pricing-note.

## 노드 종류 [vinc]
- Concept: 재사용되는 하나의 관념. 재사용되지 않을 것이면 만들지 마십시오.
- Episode: 일어난 일(세션·사건). 시간에 묶입니다.
- Record: 개념에 붙는 짧은 메모나 속성.
- Bridge: 도메인을 가로질러 묶는 매듭.
- Entity: 위 넷 중 어느 것도 아닌, 당신이 만든 노드.
그 밖의 타입도 저장되며(Entity 로), 어휘에 제안 상태로 편입됩니다.
정하지 못하겠으면 Concept 으로 넣고 그렇게 했다고 답에 적으십시오.

## 개념 하나의 크기 [core]
GOOD "낙관적 동시성 제어": 재사용되고, 자립적이고, 하나의 관념입니다.
GOOD "매몰비용 오류": 같은 모양, 다른 분야.
BAD "데이터베이스 관련": 여러 관념을 한 자루에 담았습니다. 쪼개십시오.
BAD "오늘 읽은 것": 개념이 아닙니다. Episode 입니다.
BAD "PostgreSQL" 과 "Postgres" 를 각각: 같은 것입니다. 하나만 두십시오.
기준: "X 는 ...이다" 를 두 문장으로 못 끝내면 너무 큽니다.

## 관계 [vinc]
- relates_to: 느슨한 연관. 무방향입니다(역방향은 같은 간선으로 접힙니다).
- prerequisite_of: A 가 B 보다 먼저입니다. 방향이 의미입니다.
- derives_from: A 가 B 에서 나왔습니다. 방향이 의미입니다.
- inherits_from: A 가 B 를 상속합니다. 방향이 의미입니다.
- connects_via: Bridge 에서 Concept 으로.
- about: Episode 에서 Concept 으로.
확신이 없으면 relates_to. 맞추려고 방향을 뒤집지 마십시오.

## 문서 [vinc]
원문은 vinc_doc_put 으로 넣고, 정본은 스토어가 보관합니다.
문서 전문을 Record 에 붙여넣지 마십시오. 개념은 문서를 통해 연결하고,
문서의 사본을 통해 연결하지 마십시오.

<!-- vinc:generated start · 스토어에서 재생성됩니다 · 손으로 고치지 마십시오 -->
## 이 그래프의 도메인
research (142) · product (88) · personal (31)
소문자 영어. 도메인을 즉석에서 만들지 말고 제안하고 이유를 적으십시오.

## 재가된 어휘
노드 타입: 위 다섯 외에 없음
엣지 타입: cites, contradicts
그 밖에 쓰는 것은 보존되고 제안으로 표시되어, 사람의 재가를 기다립니다.
<!-- vinc:generated end -->

## 하지 말 것 [vinc]
- 자기 제안을 스스로 재가하지 마십시오. 그것은 사람이 합니다.
- 검색해 보지 않은 것을 노드로 만들지 마십시오.
- 도메인 이름을 즉석에서 만들지 마십시오.
```

> 두 벌의 관계: **EN 이 정본**이고 ko 는 1:1 이식이다(카피 규칙 기존 관행 준수).
> 생성 구역의 숫자·목록은 언어와 무관하게 같은 소스에서 나온다.

### 2.5 생성 구역 명세

| 생성 줄 | 소스 | 경로 | 비고 |
|---|---|---|---|
| 헤더 `built` | 생성 시각 | 로컬 | 규격 버전은 상수 |
| Domains 목록 | `q.domains(owner)` | `api/query.py:280` | `{domain, episodes, concepts, records, total}` · total 내림차순 · 상위 12개 + 나머지는 "외 N개" |
| 재가 node/edge 타입 | `read_types(theme_id, status=ratified)` | `api/server.py:625` | HTTP 전용이라 **생성기는 사이드카/앱에서 돈다**(어시스턴트가 아니라) |

**"골격까지만 생성"의 경계**: 생성기는 위 3종만 쓴다. 노드 정의문·입도 예시·금지 목록은
생성하지 않는다(의미 판단이라 스토어에서 유도 불가). 자동 생성 컨텍스트 파일이 성능을
떨어뜨린다는 외부 연구(출처확인 arXiv:2602.11988, 보고서 경유 · 코딩 에이전트 대상이라
그래프 적재로의 외삽은 `추정`)를 이 경계로 흡수한다.

**빈 그래프 처리**: 도메인 0개면 생성 구역은 목록 대신 한 줄을 쓴다.
"아직 도메인이 없습니다. 첫 적재가 도메인을 만듭니다." 빈 목록을 그대로 두면 어시스턴트가
"도메인 금지"로 오독한다(`판단`).

---

## 3. 배치 (GM-2)

### 3.1 4안 비교

| 안 | 코드 비용 | 사람 개입 | 계정 그래프 추종 | 호스트 편차 | 판정 |
|---|---|---|---|---|---|
| (a) 리포 파일 | 0 | "읽어라" 필요 | 안 됨 | 없음(파일) | **Phase 1 채택** |
| (b) 스토어 문서 `graph.md` | 소(생성기 + 동기화 제외) | "가져와라" 필요 | 됨 | 없음 | **Phase 2 채택** |
| (c) MCP resource | 중(capabilities 확장) | 호스트마다 다름 | 됨 | 큼 | **보류** |
| (d) 읽기 도구 `vinc_conventions` | 소(약 20줄) | **불필요** | 됨 | 없음 | **Phase 3 채택** |

(c) 보류 근거: ① 서버가 resources 를 선언하지 않는다(`vinc_mcp/rpc.py:114`) ② resources 는
애플리케이션 제어라 모델이 스스로 못 부른다(출처확인 MCP 스펙, 보고서 경유) ③ `initialize`
응답 표면 확장은 실사고 이력이 있다(동 파일 주석, 2026-07-31 claude.ai 커넥터 연결 실패).
세 이유가 각각 독립적으로 (d)를 (c)보다 낫게 만든다.

### 3.2 (d) 설계 — 이 계획의 핵심 제안

```
vinc_conventions  (읽기 전용, readOnlyHint)
  in : {owner?}
  out: {text, source: "doc" | "default", doc_id, built_at, missing?: true}
```

- 스토어에 `graph.md` 문서가 있으면 그 본문을, 없으면 **생성 구역만 채운 기본 템플릿**을
  돌려준다. 즉 사용자가 아무것도 안 해도 도메인·재가 어휘는 전달된다.
- `vinc_ingest` 설명문 끝에 한 문장을 더한다:
  `Call vinc_conventions once before your first write in a session.`
  도구 설명은 모델 제어이고 `tools/list` 로 항상 실린다(`vinc_mcp/rpc.py:119`).
  **이 한 문장이 "아무도 안 읽는다" 실패 모드에 대한 유일한 구조적 방어다.**
- 비용: 도구 1개 추가(18 → 19)로 모든 세션이 설명문 토큰을 상시 지불한다. 설명문은 2줄 이내로
  묶는다.

### 3.3 (b) 를 켜기 전에 반드시 처리할 지뢰 2개

| 지뢰 | 코드근거 | 처리 |
|---|---|---|
| 관례 문서가 로컬↔계정으로 동기화되어 서로 덮어씀 | `api/docsync.py:32` (owner 문서 전량 열거, 제외 없음) | `export_docs` 에서 `doc_id = 'graph.md'` 제외. **각 그래프는 자기 관례를 갖는다**가 불변식 |
| 관례 문서 조각이 검색 결과·개념 파생에 섞임 | `api/docstore.py:265` 이후 청킹 | ① `origin="vinc:conventions"` 로 표식 ② 검색 제외 필요 여부는 실측 후 판단(먼저 표식만) |

두 처리 없이 (b)를 켜면 **관례 문서가 데이터를 오염시키는 쪽으로 작동한다.** Phase 2 의
수용 기준에 넣는다.

---

## 4. 정본 (GM-3)

**스토어가 정본. GRAPH.md 는 파생 뷰.** 다만 "전부 파생"은 틀렸다. 정확히는 **구역별로 정본이
다르다**:

| 구역 | 정본 | 편집 주체 | 어긋나면 |
|---|---|---|---|
| 생성 구역(도메인·재가 어휘) | 스토어 | 생성기 전용 | lint `generated-drift` 실패 → 재생성 |
| 산문 구역(정의·입도·금지) | **이 파일** | 사람 | 스토어에 대응물이 없다(비교 불가) |

이 이중성을 문서에 못 박지 않으면 "문서는 전부 파생인데 왜 손으로 쓰나"라는 모순이 남는다.
한 문장으로 규정: **"스토어가 아는 것은 스토어가 쓴다. 스토어가 모르는 것만 사람이 쓴다."**

---

## 5. 검증 (GM-4)

만든다. 단 **형상·참조 무결성만**. 의미 검증은 넣지 않는다(원칙 위반).

### 5.1 규칙 9개

| # | 규칙 | 수준 |
|---|---|---|
| 1 | `header-marker` 첫 줄 마커·규격 버전 파싱 가능 | BLOCK |
| 2 | `section-order` 필수 절 존재·순서 | BLOCK |
| 3 | `generated-block` 시작/끝 마커 쌍·순서 | BLOCK |
| 4 | `generated-drift` 생성 구역 내용 ≠ 현재 스토어 census | BLOCK |
| 5 | `unknown-edge` 관계 절의 이름이 wire 6종 + 재가 어휘 밖 | BLOCK |
| 6 | `unknown-domain` 도메인 절 항목이 실 데이터에 없음 | FIX |
| 7 | `stale-tool` 본문의 `vinc_*` 가 `TOOLS` 에 없음 | FIX |
| 8 | `length` 60줄 초과 | FIX / 100줄 초과 BLOCK |
| 9 | `forbidden-glyph` em-dash 등 화면 금지 문자(D38) | FIX |

규칙 4·5·6·7 은 스토어·코드를 읽어야 하므로 **검증기는 사이드카 옆에서 돈다**.

### 5.2 CLI

```
python -m api.cli graph-md build  [--out FILE | --doc]   # 생성/갱신
python -m api.cli graph-md check  [--file FILE | --doc]  # 9규칙 검사
```

exit code 는 기존 게이트와 맞춘다: `0` PASS · `1` FIX-only · `2` BLOCK
(선례 `Vinc-docs/CONVENTIONS.md §3`). `--doc` 는 스토어의 `graph.md` 를 대상으로 한다.

---

## 6. DOCS 설명 계획 (GM-5)

### 6.1 IA 결정 — **개정 2026-08-01(발주자 지시)**

초안은 "MCP 섹션 3번째 페이지, 독립 섹션은 180줄 넘을 때 검토"였다. **발주자가 독립 섹션으로
확정**했다(참조: Stitch 의 DESIGN.md 문서가 제품 문서 안에서 자기 섹션을 갖는 방식).

- `SECTIONS` 가 넷이 된다: **Vinc · MCP · CLI · GRAPH.md**.
- 4쪽: `/docs/graph-md/`(무엇인가) → `/build/`(만들기) → `/spec/`(규격) → `/check/`(검사).
- Stitch 6쪽 대응: What is → 무엇인가 · Import from your codebase → 만들기 ·
  The specification → 규격 · Validate with the CLI + Linting rules → **검사(둘을 한 쪽으로 합침)** ·
  View/edit/export → 만들기의 §앱에서·§명령줄에서로 흡수.
- 초안 경로 `/docs/mcp/graph-md/` 는 **배포 전이라 리다이렉트가 필요 없다**(로컬에만 있었다).
  대신 링크 5곳(개요 카드·use-cases·mcp·cli·tools)을 새 경로로 고쳤다.
- **Stitch 본문 실견(2026-08-01, Claude in Chrome).** 텍스트 추출·a11y 트리는 여전히 iframe 에
  막히지만 **화면으로는 읽힌다**(스크린샷 계측). 확인한 것:
  - 섹션 전환 = `Stitch · MCP · SDK · DESIGN.md · Agent Skills` — 우리 4섹션과 같은 자리매김.
  - DESIGN.md 6쪽 = 프롬프트에 적힌 목록과 일치.
  - 개요 쪽 = Overview · What it gives you · The philosophy · How they're created(3경로:
    에이전트 생성 · 브랜딩에서 유도 · 손으로) · Example.
  - **이층 구조가 우리와 동형**: YAML front matter(기계값) + markdown body(근거).
    "Tokens give agents precise values. Prose tells them *why* those values exist."
  - 철학: "a foundation, not a prescription … **Unknown sections and custom tokens are accepted,
    not rejected**" · "a **living artifact**, not a static config file".
  - 규격 쪽 목차에 **`Consumer behavior for unknown content`** 와 `Section order` 가 있다.
  - ⚠ `Validate with the CLI`·`Linting rules` 두 쪽은 좌측 내비 클릭이 먹지 않아 **못 열었다**
    (URL slug 도 불명 — `/validate/` 404). 그들의 규칙 목록과 우리 9규칙의 항목 대조는 미완.
- **반영(2026-08-01)**: 개요에 §바닥이지 울타리가 아닙니다(긍정형 철학 + living file) ·
  규격에 §규격에 없는 것을 만나면(unknown content 대응) · 만들기에 §들어가는 길 셋
  (무노력→전부 직접, 그들의 "effortless to precise" 대응).

### 6.2 페이지 구성 (h2 6개, Stitch 대응)

**as-built (2026-08-01 배포 대기).** 초안의 `make`·`check` 절은 **빼고 냈다.** 그 두 절이
설명할 `graph-md build/check` 는 Phase 2 산출물이라 지금 없다. **없는 기능을 설명하는 문서가
바로 이 프로젝트가 막으려는 실패**라, 페이지는 오늘 되는 것만 적는다.

| # | id | EN 제목 | 담은 내용 | Stitch 대응 |
|---|---|---|---|---|
| 1 | `what` | What GRAPH.md is | 증상부터: 같은 자료를 두 어시스턴트가 다르게 넣는다. 의미 판단은 여전히 어시스턴트가 한다 | What is |
| 2 | `spec` | What goes in it | 두 구역(사람/생성) + 절 8행 + 60줄 규칙 | The specification |
| 3 | `tpl` | The file | 템플릿 전문 코드블록(EN 정본 · ko 사전) | (Vinc 고유) |
| 4 | `read` | Getting your assistant to read it | 오늘 되는 세 자리: 리포 파일 · 대화 첫 메시지 · Vinc 문서 | View, edit, export |
| 5 | `keep` | Keeping it true | 드리프트 = 거짓말하는 문서. "스토어가 아는 것은 스토어에서 옮겨 적는다" | (정본 규칙 §4) |
| 6 | `limits` | What this file does not do | 판단 대행 안 함 · 강제 안 함 · 자기재가 불가 · 기기와 계정은 각자의 관례 | (Vinc 고유) |

Phase 2·3 이 나오면 `make`(생성기)와 `check`(검증기) 절을 이 페이지에 **추가**한다.
그때도 페이지가 180줄을 넘으면 그 시점이 독립 섹션 분리 검토 시점이다(§6.1).

6번은 Stitch 에 없다. Vinc 는 "의미 판단은 어시스턴트"가 제품 원칙이라, **이 문서가 그 원칙을
꺾지 않는다는 것을 문서가 직접 말해야 한다.** 이 절이 Q6 의 4번째 실패 모드(원칙 무력화)에
대한 공개된 방어다.

### 6.3 카피 원칙

- **EN 마크업이 정본, ko 는 `DOCS_DECK` 사전**(기존 10쪽 전부 이 형식).
- em-dash 금지(D38). 템플릿 코드블록 안까지 포함.
- 분량 130~160줄(현행 페이지 중앙값 133, 최장 194).
- 어투: 기존 ko 페이지와 동일한 존댓말·짧은 문장. "~합니다".
- 용어: "온톨로지"(기존 페이지 표기) 유지. "그래프"와 혼용하지 않는다.

### 6.4 배포 체크리스트

1. `site/docs/mcp/graph-md/index.html` 신설(본문 + `DOCS_DECK.ko`).
2. `site/assets/docs.js` `SECTIONS.mcp.items` 에 항목 추가(IA 정본).
3. `sitemap.xml` 수동 갱신(자동 아님 — docs.js 주석 명시).
4. 포인터 3곳: `/docs/` 의 "The map of these docs" 한 줄 · `/docs/use-cases/` 의 "구조화는
   어시스턴트가 합니다" 문단 · `/docs/mcp/` 의 "Checking it worked" 다음 한 문단.
5. 링크 검사 후 홈페이지 배포(릴리스↔홈페이지 즉시 결합 규칙 준수 — `DEPLOYMENT.md`).

### 6.5 내부 설계 문서

- 신설: `Vinc-docs/vinc/C/vinc.c.graph-md.md` (C 계층 = 제품 Delta·계약).
  선례 `vinc.c.chunk-ssot`(D45 계약)와 같은 자리.
- `master-id-map.yml` 에 **VINC-C-011** 로 등재(현재 C-010 까지 사용).
- 프론트매터는 C 계층 관행 준수(`doc_id/layer/domain/title/status/authority/version/owner/
  last_updated/inherits_from/tags`), `inherits_from: [vinc.c.chunk-ssot, vinc.mcp]`.
- `vinc/C/README.md` 목록에 한 줄 추가.

---

## 7. 실행 순서

| Phase | 산출물 | 코드 | 수용 기준 |
|---|---|---|---|
| **0. 결정** | GM-1 채택 · 템플릿 EN/ko 확정 | 0 | ✅ **2026-08-01 확정** |
| **1. 파일 + 문서** | `vinc.c.graph-md.md`(C-011) · 템플릿 2벌 · `/docs/mcp/graph-md/` · 포인터 3곳 · NAV · sitemap | 0(홈페이지만) | ✅ **완료** — 로컬 검증: 콘솔 오류 0 · em-dash 0 · TOC 6 · 이전/다음(도구↔레퍼런스) · EN/ko 양쪽 렌더 · 템플릿 58줄 |
| **2. 생성기 + 검증기** | `api/graphmd.py` · `graph-md build/check` · `origin` 표식 · **docsync 제외(양방향)** · 문서 페이지 §make·§check | 소 | ✅ **완료 2026-08-01** — `tests/test_graphmd.py` 18건 · 리포 전량 615 passed |
| **3. 도구화** | `vinc_conventions` · `/conventions` · 프록시 허용목록(허브+계정) · `vinc_ingest` 설명문 1문장 | 소 | ✅ **완료 2026-08-01** — 도구 19종 계약 테스트 갱신 · 실 HTTP 라우트 테스트 · 전량 625 passed |
| **4. 판정** | 4주 뒤 지표 대조(§8) | 0 | 개선 없으면 §8 중단 기준 발동 |

Phase 1 만으로도 완결된 산출물이다(문서 + 템플릿). Phase 2·3 은 GM-1 이 통과한 뒤에 붙인다.

---

## 8. 측정 설계

연구 보고서의 "proposed_types 발생률"은 **오늘 못 잰다.** 로컬 우선 제품이라 적재 이벤트
텔레메트리가 없다. 대신 **스토어를 스냅샷으로 재는 지표 2개**로 바꾼다. 둘 다 기존 라우트로
계산된다.

| 지표 | 정의 | 소스 | 방향 |
|---|---|---|---|
| **P** 제안 적체 | `status=proposed` 타입 수 | `read_types(status)` `api/server.py:625` | 감소 |
| **D** 도메인 분열 | 정규화(소문자·공백·언어 변형) 후 한 그룹에 2개 이상 들어가는 그룹 수 | `q.domains` `api/query.py:280` | 감소 |

- **측정 방법**: Phase 1 배포 직전 스냅샷 1회, 이후 주 1회 4주. 발주자 자신의 그래프 기준
  (n=1 이라 통계가 아니라 **관측**이다. 이 한계를 결과에 함께 적는다).
- **P 의 함정**: 재가를 안 하면 P 는 어차피 는다. 재가 이벤트와 함께 봐야 한다. 그래서
  보조 지표로 **신규 제안 타입 수 / 주**를 손으로 센다.
- **중단 기준**: Phase 3 까지 켠 뒤 4주에 P·D 어느 쪽도 개선이 없으면 GRAPH.md 를 접고
  대안으로 전환한다 — **대안: `vinc_ingest` 응답의 `proposed_types` 를 "왜 제안 상태인지 +
  가장 가까운 재가 어휘"로 강화**(코드근거 `api/fragment.py:484` 부근). 사전 예방 대신
  사후 교정 루프를 개선하는 쪽이다.

### 8.1 GM-6 실측 프로토콜 (코드 0)

GM-6 은 판단으로 가르지 말고 **재고 결정한다.** 코드 변경 없이 오늘 잴 수 있다.

**표본** N=20. 연도가 본문에 명시된 것 10 · 모호한 것 10(추정은 가능하나 단정 불가한 것).
모호 쪽이 이 실험의 본체다. 명시 쪽은 대조군이다.

**정답지**: 사람이 이미 `content_time` 을 채운 문서가 있으면 그 값이 정답지다
(`vinc_doc_list` 가 돌려준다 — §1.2-6). 없으면 발주자가 표본에 대해서만 손으로 만든다.

**블라인드 조건** (셋 다 지켜야 결과가 유효하다):
- 측정하는 어시스턴트는 **새 세션**에서 돈다. 이 대화는 위험을 이미 알고 있어 측정자가 될 수 없다.
- 프롬프트에 "지어내지 마라" · "모르면 비워라" 를 **넣지 않는다**. 그 문장이 있으면 재는 것이
  모델의 성향이 아니라 그 문장의 효과다.
- `vinc_doc_list`·`vinc_doc_get` 호출 금지. 문서는 텍스트로 건넨다(정답지가 도구에 실려 있다).

**지표 2개**

| 지표 | 정의 | 판정선 |
|---|---|---|
| **A** 오답률 | 명시 10건 중 정답지와 다른 값 | 1건이라도 틀리면 노출 반대 근거로 충분 |
| **B** 모호시 공란률 | 모호 10건 중 값을 비운 비율 | **이 결정의 본체.** 0.7 미만이면 지어내는 성향 |

B 가 낮게 나오면 노출은 **틀린 값을 만드는 기능**이 된다. D72 가 "본문에서 연도를 채굴하는
기능은 범위 밖"이라고 못 박은 이유가 그것이고, 지어낸 연도는 틀린 줄 모르는 값이라
사후 교정 루프에도 안 걸린다(사람이 대조하지 않는 한 영원히 남는다).

**이 세션에서 못 돌린 이유**(실측): 로컬 스토어가 비어 있고(`vinc_doc_list` → `count: 0`)
원격은 키가 없다(`VINC_REMOTE_MCP_TOKEN` 미설정). 표본은 실 데이터가 있는 기기에서 뽑아야 한다.

**선택지 3개** (실측 뒤 고른다)

| 안 | 내용 | 비용 | 언제 |
|---|---|---|---|
| 노출 | `_t_doc_put` 에 `content_time` 전달 + 스키마 1필드 | 2줄 | A=0 이고 B≥0.7 |
| 미노출 | 앱·HTTP 전용 유지. 템플릿은 "읽기"만 언급 | 0 | B<0.7 |
| 제안·재가 축 | 어시스턴트는 제안만, 사람이 재가 | **작지 않다**(아래) | B<0.7 인데도 필요할 때 |

세 번째 안의 비용을 정직하게 적는다. 기존 재가 기구는 **타입 이름 어휘 전용**이다
(코드근거 `Vinc/store/theme_store.py:177` `propose_type(theme_id, kind, name)`, kind ∈ node|edge).
문서 필드 값은 그 축에 그대로 못 올린다. 실제로 드는 것: `_DocMeta` 열 1개 + 마이그레이션 +
동기화 열 전파(`api/docsync.py:37` 계열) + 앱 재가 UI + HTTP 라우트.
다만 **개념적 선례는 있다** — 문서에서 파생된 Concept 은 이미 `status='proposed'` 로 들어간다
(코드근거 `Vinc/api/docstore.py:907`). 원칙을 새로 만드는 게 아니라 배관을 까는 일이다.

**순서**: GM-6 은 GM-1 의 블로커가 아니다. 템플릿에서 그 줄을 이미 뺐으므로 GM-1·Phase 1 을
먼저 확정하고, 이 실측은 Phase 2 착수 전 아무 때나 끼워 넣으면 순서가 깨지지 않는다.

---

## 9. 열린 결정 (DEC 후보)

> **확정 2026-08-01.** GM-1~GM-5 확정, Phase 1 완료. 규격 정본은
> `Vinc-docs/vinc/C/vinc.c.graph-md.md`(VINC-C-011) 로 이관했고, 이 계획서는 근거·판정 기록으로
> 남는다. 신규 **GM-8**(재가 표면)은 그 문서 §9.4 에서 나왔다.

| # | 질문 | 이 계획의 권고 | 확정 전까지 |
|---|---|---|---|
| GM-1 | GRAPH.md 채택 | **채택 확정.** 근거는 §1.2-1(어휘 채널이 비어 있다) | ✅ |
| GM-2 | 배치 | (a) → (b) → **(d)**. (c) 보류 | §3 |
| GM-3 | 정본 | 스토어(생성 구역) + 문서(산문 구역)의 **구역별 정본** | §4 |
| GM-4 | CLI 검증 | 만든다. 형상 9규칙만 | Phase 2 |
| GM-5 | 문서 사이트 | 새 섹션 안 만든다. MCP 섹션 3번째 페이지 | Phase 1 |
| **GM-6** | `content_time` 을 MCP `vinc_doc_put` 에 노출하는가 | **실측으로 가른다(§8.1).** 판단 대신 A·B 두 수치로 3안 중 고른다. GM-1 의 블로커가 아니다 | Phase 2 착수 전 |
| **GM-7** | 관례 문서를 검색 결과에서 제외하는가 | 먼저 `origin` 표식만. 실측 후 판단 | Phase 2 이후 |

GM-6 은 비용이 갈린다: 노출은 `tools.py` 1줄 + 스키마 1필드지만, **어시스턴트가 내용 시점을
지어내기 시작하는 위험**을 연다(본문에서 연도 채굴은 범위 밖이라고 D72 가 못 박았다).
지어낸 연도는 틀린 값이 아니라 **틀린 줄 모르는 값**이라 되돌리기 어렵다.
권고를 내지 않는 것은 정보가 없어서가 아니라 **아직 안 쟀기 때문이다.** §8.1 이 그 측정을
코드 0으로 정의한다. 재고 나면 이 칸은 판단이 아니라 A·B 두 수치로 채워진다.

---

## 10. 미해결

- **호스트 실측 미수행.** Claude Desktop·Cursor·ChatGPT 가 resources 를 실제로 어떻게 다루는지
  이 세션에서 재지 않았다(`추정`, 보고서 인용에 의존). 다만 §3.1 의 판정은 resources 지원 여부와
  **무관하게** 성립한다((d)가 프로토콜 확장 없이 동작하므로). 실측이 필요해지는 시점은
  GM-2 가 (c)를 다시 꺼낼 때뿐이다.
- **템플릿 효과 미검증.** 입도 예시 5개가 실제로 적재 모양을 바꾸는지는 `추정`이다.
  §8 의 P·D 로 4주 뒤 확정된다.
- **다국어.** 사용자가 한국어로 그래프를 쓰면 GRAPH.md 도 ko 여야 하는가. 생성 구역은 언어
  무관이지만 산문 구역은 아니다. Phase 1 은 두 벌 다 배포하고 **선택은 사용자에게** 둔다.
- **팩과의 관계.** `.vincpack` 이 관례 문서를 함께 실어야 하는지 판단하지 않았다. 커뮤니티 팩을
  가져올 때 남의 관례가 내 그래프를 덮는 문제가 생긴다(§3.3 동기화 지뢰와 같은 계열).
  Phase 2 착수 전에 결정해야 한다.
