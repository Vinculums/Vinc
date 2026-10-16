/** 노드가 속한 주제 경로. `GraphNode`(api.ts) 곁의 헬퍼인데 api.ts 에 두지 않는다:
 *  api.ts 는 모듈 평가 때 `location` 을 읽어서, 배치 서비스(`layout-service.ts`, 허브의
 *  노드 컨테이너)와 단독 트랜스파일 테스트가 이 함수를 쓰려면 import 가 없는 자리여야 한다.
 *
 *  주제 노드(`domain:<경로>`)는 자기 id 가 곧 경로이고, 나머지는 `domain` 열이 경로다.
 *  둘 다 없으면 빈 문자열. */
export const DOMAIN_PREFIX = "domain:";

export const topicOf = (n: { id: string; kind?: string; domain?: string }): string =>
  n.kind === "domain" || n.id.startsWith(DOMAIN_PREFIX) ? n.id.slice(DOMAIN_PREFIX.length) : n.domain || "";
