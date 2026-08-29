> **초안 · 공개는 W18(스코프 키) 이후.** 현재 Vinc 키는 `vinc_sk` 전권(읽기+쓰기)
> 한 종류뿐이다. 읽기 전용 스코프 키 티어(`decision:scoped-key-tier`)가 출시되기
> 전에는 이 가이드를 공개하지 않으며, **전권 키를 OpenClaw 설정에 넣는 것을 권장하지
> 않는다.** OpenClaw 는 공개 노출·악성 스킬 사고 이력이 있는 호스트 클래스다
> (근거: `record:openclaw-research-review-2026-08-29` finding 3).

# Vinc x OpenClaw 등록 가이드 (초안)

Vinc 계정 그래프를 OpenClaw 에이전트의 메모리로 붙이는 두 경로를 다룬다.
두 경로는 배타적이지 않고, 어느 쪽도 내장 memory-core 를 대체하지 않는다
(경로 판정: `decision:openclaw-integration-corpus-supplement`).

| 경로 | 도달 방식 | 쓰기 |
|---|---|---|
| 1. 표준 MCP 등록 | 에이전트가 Vinc 도구를 스스로 호출 | 명시적 도구 호출로만 |
| 2. 코퍼스 supplement 플러그인 | `memory_search`/`memory_get` 이 자동으로 Vinc 코퍼스에 도달 | 없음 (읽기 전용) |

어느 경로든 **자동 추출은 없다**: 대화에서 사실을 추출해 자동 저장하는 기능은
만들지 않는다. 그래프에 들어가는 것은 사람 또는 에이전트가 명시적으로 확정해
쓰기 도구를 부른 입력뿐이다.

## 1. 표준 MCP 등록 (모든 MCP 클라이언트 공통)

`openclaw.json` 에 아래를 추가한다:

```json
{
  "mcpServers": {
    "vinc": {
      "url": "https://mcp.vincs.io/mcp",
      "headers": { "Authorization": "Bearer <VINC_API_KEY>" }
    }
  }
}
```

`vinc_search` · `vinc_doc_get` · `vinc_graph` 등 Vinc 도구가 내장 도구와 나란히
노출된다. 한계: 에이전트가 도구를 부르기로 스스로 판단해야 하며(자동 회수 없음),
도구 정의가 컨텍스트 토큰을 소모한다.

> 확인 필요(공개 전): `mcpServers` 항목의 필드명(`url`/`headers`)은 공개 시점의
> OpenClaw 버전 문서와 실왕복으로 재검증할 것.

## 2. 코퍼스 supplement 플러그인 (OpenClaw 전용)

이 디렉터리의 플러그인은 `api.registerMemoryCorpusSupplement(...)` 로 등록되는
**비배타** 코퍼스다. 배타 memory 슬롯을 차지하지 않고 memory-core 를 대체하지
않으면서, `memory_search` 와 `memory_get` 이 Vinc 계정 그래프(문서·노드·관계)에
자동으로 도달하게 한다. 같은 경로를 쓰는 선례가 번들 플러그인 memory-wiki 다.

설정 (`plugins.entries.vinc.config`):

```json
{
  "plugins": {
    "entries": {
      "vinc": {
        "config": {
          "apiKey": "<VINC_API_KEY>",
          "url": "https://mcp.vincs.io/mcp"
        }
      }
    }
  }
}
```

- `apiKey` (필수): Vinc 계정 키. **스코프 키(W18) 출시 전까지는 전권 키뿐이므로
  등록을 권장하지 않는다.** 로컬 개발에서는 `VINC_REMOTE_MCP_TOKEN` 환경변수
  폴백이 있다.
- `url` (선택): 허브 엔드포인트. 기본 `https://mcp.vincs.io/mcp`.
- `maxResults` (선택): 검색 기본 상한. 기본 8.
- `timeoutMs` (선택): HTTP 타임아웃. 기본 10000.

검색 결과의 `path` 는 `vinc://doc/<doc_id>` 또는 `vinc://node/<node_id>` 형태이고,
`memory_get` 에 그대로 넘기면 문서 전문 또는 노드 상세(속성·관계·인용)를 받는다.

## 3. 인용 게이트 시맨틱

Vinc 가 돌려주는 재료에는 항상 출처가 붙는다. 소비하는 에이전트(brief 응답 포함)는
다음 규칙으로 인용한다:

1. **모든 결과에 `citation`(문서 id 또는 노드 id)이 붙는다.** 노드 상세에는
   `updated_at` 시점이 함께 온다. 주장에 이 인용을 그대로 부착한다.
2. **근거(evidence)가 있는 관계는 근거 문장을 인용한다.** 노드 상세의 관계 줄에
   `[evidence: "..."]` 로 표기된다.
3. **근거가 없는 관계는 거부하지 않고 `[unsourced]` 로 라벨한다.** 근거 없는
   주장을 거부형 게이트로 차단하면 현 그래프 상태(관계 근거 커버리지 2.8%)에서
   관계 인용의 약 97% 가 차단된다. 커버리지가 오를 때까지, 게이트의 역할은
   차단이 아니라 **소비자에게 근거 유무를 정직하게 표시하는 것**이다
   (근거: `record:openclaw-research-review-2026-08-29` finding 2).
4. unsourced 라벨이 붙은 재료로 답할 때는 응답에도 그 라벨을 전파한다.
   "그래프에 있으나 근거 문장은 없다" 와 "근거가 인용된다" 는 다른 신뢰 등급이다.

## 안 한 것 (사람 게이트)

- 라이브 OpenClaw 게이트웨이에 플러그인을 실제 로드하는 e2e (pi-embedded 런너에서,
  사람 판단으로 실행). 단위 테스트는 플러그인 API 와 허브를 모킹한다: `npm test`.
- 공개 배포(패키징·레지스트리 게시). W18 이후.
- `mcpServers` 설정 필드명의 실왕복 검증 (위 1절의 확인 필요 항목).
