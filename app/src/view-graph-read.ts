/** 그래프 보기가 하는 읽기는 `/graph` 하나다. 서버가 받는 인자와 재사용 키가 같은 값에서
 * 나오므로 한 곳에서 계산한다: 둘이 갈리면 다른 요청이 같은 캐시 항목을 쓴다.
 * 좌표는 서버의 `hub` 배치 하나를 받는다(나침반은 받은 그래프를 브라우저에서 스스로 놓는다).
 * ⚠ `scopeKey` 의 모양을 바꾸지 않는다: 탭 보관소와 스페이스 보관본(IndexedDB)이 이 문자열로 항목을 찾는다. */
type GraphReadPlan = {
  scopeKey: string;
  options: { domains?: string[]; layout: "hub" };
};

export function graphReadPlan(domains: string[]): GraphReadPlan {
  return {
    scopeKey: JSON.stringify({ domains: [...domains].sort(), layout: "hub", cone: 1 }),
    options: { ...(domains.length ? { domains } : {}), layout: "hub" },
  };
}
