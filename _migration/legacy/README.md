# legacy/ — Vinc-docs 폐기 시 건져 온 것

`Vinc-docs` 를 지우기 전(2026-08-11) 그 리포에서 **`vinc/` 구 코퍼스를 뺀 전부**를
그대로 옮겨 왔다. 605K.

`vinc/`(802K) 를 뺀 이유는 하나다: 그건 P5 이행의 대상이었고 **재작성본이 그래프에
54/54 전부 살아 있다**(`vinc_doc_list` 로 대조 확인). 원장(`../ledger-documents.tsv`)
이 구 id ↔ 신 id 를 다 들고 있으므로 계보도 끊기지 않는다. 같은 것을 두 벌 두지 않는다.

여기 있는 것은 **그 대조에서 빠져 있던 것들**이다.

| | 무엇 | 왜 건졌나 |
|---|---|---|
| `plans/` | 계획서 7 · 발주 프롬프트 4 | 아래 참조 |
| `DESIGN/` | 아이콘 43 · Figma 내보내기 43 · 갤러리 · 감사 문서 4 | 2026-07-23 자산. 앱이 쓰는 22종 말고 나머지가 여기밖에 없다 |
| `CONTEXT/layer-config.md` | 계층 설정 | 대응 없음 |
| 루트 `*.md`·`*.yml` | `PLAN.md`·`master-id-map.yml` 외 | `PLAN.md` 는 CLAUDE.md 가 "마일스톤 정본"으로 지목하던 파일이고, `master-id-map.yml` 은 id 매핑이라 원장 계열이다 |

## plans/ 가 원장에 없던 이유

원장은 **그래프에 이미 적재된 문서**를 훑어 만들어졌다. 그래서 적재된 적 없는 파일은
원장의 시야 밖이었다. 11건 중 4건은 2026-08-01 에 문서로 적재돼 있어서 이행을 탔고
(`vinc.plan.code-signing` · `embedding-pipeline` · `figma-build` ·
`graph-conventions`), 나머지 7건은 파일로만 존재했다:

    PLAN-newsletter-signin-ui.md        완료·라이브 (as-built 는 vinc.spec.web-system)
    PLAN-user-id-username.md            핸들 도입 (일부가 canon.account-policy 에)
    PLAN-web-account-email-backend.md   완료·라이브 (as-built 는 vinc.spec.email-subsystem)
    PROMPT-deploy-e5-hub.md             발주 프롬프트
    PROMPT-deploy-graph-md.md           발주 프롬프트
    PROMPT-embedding-retrieval-sequencing.md
    PROMPT-graph-md.md                  vinc.plan.graph-conventions 의 입력

결과(as-built)는 전부 그래프에 있고, 여기 없는 것은 **그때의 판단 기록**이다.
`PROMPT-*` 는 지식이 아니라 발주 입력이라 파일이 제자리다.

⚠ **여기를 지식 조회처로 쓰지 마라.** 낡았다. 지식은 `vinc_search` 로 그래프를 봐라.
이 디렉터리의 용도는 하나다 — "그때 무엇이 있었는지" 를 나중에 되짚는 것.
