> **읽기 전용 키를 쓸 것.** 스코프 키 티어(W18, `decision:scoped-key-tier`)는
> 2026-08-30 부터 라이브다. OpenClaw 에 넣는 키는 `vinc_ro_` 로 발급하고,
> **전권 `vinc_sk` 키를 넣지 말 것** — OpenClaw 는 공개 노출·악성 스킬 사고 이력이
> 있는 호스트 클래스다(근거: `record:openclaw-research-review-2026-08-29` finding 3).
> 스코프는 허브가 도구 디스패치에서 fail-closed 로 집행한다: 스코프를 확인할 수 없는
> 키는 쓰기 도구가 거절된다.

# Vinc x OpenClaw 등록 가이드

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

`~/.openclaw/openclaw.json` 의 `mcp.servers` 아래에 추가하고 게이트웨이를 재시작한다:

```json
{
  "mcp": {
    "servers": {
      "vinc": {
        "url": "https://mcp.vincs.io/mcp",
        "transport": "streamable-http",
        "headers": { "Authorization": "Bearer <VINC_RO_KEY>" }
      }
    }
  }
}
```

`transport` 는 필수다. 빼면 OpenClaw 기본값이 SSE 인데 Vinc 허브는 Streamable HTTP
만 내므로 연결이 실패한다.

`vinc_search` · `vinc_doc_get` · `vinc_graph` 등 Vinc 도구가 내장 도구와 나란히
노출된다. 한계: 에이전트가 도구를 부르기로 스스로 판단해야 하며(자동 회수 없음),
도구 정의가 컨텍스트 토큰을 소모한다.

> 2026-09-05 문서 대조 완료: 이 문서의 이전 판은 최상위 `mcpServers` 를 적었는데
> OpenClaw 설정 레퍼런스(docs.openclaw.ai/gateway/configuration-reference · cli/mcp)는
> `mcp.servers` 이고 `transport` 기본이 SSE 다. 위 블록이 그 정정이다. 라이브 OpenClaw
> 게이트웨이 왕복은 아직 안 걸었다(아래 "안 한 것").

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

- `apiKey` (필수): Vinc 계정 키. **읽기 전용 키(`vinc_ro_`)를 쓸 것** — 계정
  페이지에서 발급한다. 이 플러그인은 읽기만 하므로 전권 키가 필요 없고, 전권 키를
  넣으면 호스트가 침해될 때 그래프 쓰기까지 함께 넘어간다. 로컬 개발에서는
  `VINC_REMOTE_MCP_TOKEN` 환경변수 폴백이 있다.
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
3. **근거가 없는 관계는 거부하지 않고 `[unsourced]` 로 라벨한다.** 2026-08-30 센서스
   (`record:edge-evidence-coverage-2026-08-30`, 일곱 도메인·88 관계)에서 출처 문장을 가진
   관계는 20건이다(전체의 23%, 문장을 둘 자리가 있는 관계 종류만 치면 36%). 근거 없는
   주장을 거부형 게이트로 차단하면 관계 인용의 대부분이 차단된다. 커버리지가 오를 때까지,
   게이트의 역할은 차단이 아니라 **소비자에게 근거 유무를 정직하게 표시하는 것**이다
   (근거: `record:openclaw-research-review-2026-08-29` finding 2).
4. unsourced 라벨이 붙은 재료로 답할 때는 응답에도 그 라벨을 전파한다.
   "그래프에 있으나 근거 문장은 없다" 와 "근거가 인용된다" 는 다른 신뢰 등급이다.

## 안 한 것 (사람 게이트)

- 라이브 OpenClaw 게이트웨이에 플러그인을 실제 로드하는 e2e (pi-embedded 런너에서,
  사람 판단으로 실행). 단위 테스트는 플러그인 API 와 허브를 모킹한다: `npm test`.
- 공개 배포(패키징·레지스트리 게시). W18 은 풀렸으므로 이제 남은 것은 배포 작업
  자체이지 선행조건이 아니다.
- 1절 `mcp.servers` 블록의 라이브 OpenClaw 게이트웨이 왕복 (필드명은 2026-09-05 에
  공식 레퍼런스와 대조했고, 실제 연결은 아직이다).
