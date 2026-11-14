// Agent 화면이 노드 id 를 짓고 알아볼 때 쓰는 접두. 역할·절차·정책 id 의 모양은 여기 한 곳에서 읽는다.
// 같은 값이 허브에도 있다: `api/role_contract.py` 의 `ID_PREFIX`, `api/roles.py` 의 `PROCEDURE_PREFIX`·`POLICY_PREFIX`.
// 접두를 바꾸면 그쪽과 같이 바꾼다.

/** 역할 id 접두(`concept:agent-dev-core`). 로스터 개념 `concept:agent-orchestration` 도 접두가 같다. */
export const AGENT_ID_PREFIX = "concept:agent-";
/** 절차 id 접두(`concept:procedure-build-a-package`). */
export const PROCEDURE_ID_PREFIX = "concept:procedure-";
/** 정책 id 접두(`decision:policy-read-only-procedures`). */
export const POLICY_ID_PREFIX = "decision:policy-";

/** 앞쪽 접두만 뗀다. 접두로 시작하지 않으면 그대로 돌려준다(가운데 같은 글자가 있어도 건드리지 않는다). */
export const withoutPrefix = (id: string, prefix: string): string =>
  id.startsWith(prefix) ? id.slice(prefix.length) : id;
