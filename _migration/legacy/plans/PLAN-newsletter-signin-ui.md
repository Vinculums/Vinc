# 뉴스레터 가입 UI + Sign-in 노출 결정 계획서

> ✅ **완료 — 2026-07 프로덕션 라이브.** 본 계획서는 빌드 당시 기록으로 보존한다.
> **as-built 정본 = [[vinc.c.web-vincs-app]]**(`vinc/WEB-vincs-app.md`).
> 아래 "현재 상태"는 **더 이상 사실이 아니다**: 홈에 뉴스레터 구독 폼이 **있고**(`POST /api/subscribe`
> 실배선), `/login`·`/account` 는 목업이 아니라 **실동작**(매직링크 발급→verify→세션, ARCH §5.2).
> 또한 발신 주소는 본문의 `vincs@vincs.app` 이 아니라 **`news@vincs.app`** 로 확정됐다.
> 본문이 인용하는 `Vinc/site/index.html` 경로·행번호는 **분리 이전 구본** 기준이다
> → 정본 = `Vinc-homepage/site/`.

> 목표: 홈페이지에 "릴리스 소식" 이메일 구독 블록(더블 옵트인, 동일 출처 `/api/subscribe` POST, CSP-safe)을 추가하고, 앱은 "무로그인"이라는 히어로 메시지와 충돌 없이 웹 "Sign in" 진입점을 어디에 둘지 확정한다.
> 현재 상태: `/login`(매직링크 폼, `onsubmit="return false"` 목업)·`/account`(가짜 로그인 화면 + "릴리스 소식" 토글)은 이미 존재·라이브. 홈에는 구독 진입점이 **없음**. 실제 제출은 계정 API(`PLAN-web-account-email`)에 하드 의존 → API 준비 전엔 UI만 빌드하고 제출은 비활성/목업.

---

## 전제·의존 (preconditions / dependencies)

| # | 항목 | 상태 | 시작 차단 여부 |
|---|------|------|----------------|
| P1 | **정적·CSP 불변식**: `default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'` — 외부 요청·`data:` URI 금지. 인라인 `<script>`/`<style>`은 허용, 동일 출처 `fetch('/api/subscribe')`는 `default-src 'self'`로 허용됨. | 확정 | 차단 아님(설계 제약) |
| P2 | **동일 출처 엔드포인트 필수**: `POST /api/subscribe`가 `vincs.app` 같은 오리진에서 응답해야 함(Caddy가 `/api/*` → 계정 서비스로 reverse_proxy). 서브도메인/외부 폼 서비스(Mailchimp 등)는 CSP·정적 불변식 위반 → **불가**. | `PLAN-web-account-email`에 요청 필요 | Phase 4만 차단(1~3은 무관) |
| P3 | **더블 옵트인·발신 메일박스**: `vincs@vincs.app` 검증 완료 → 확인 메일 발송은 계정 API 소관. 프런트는 이메일 캡처·POST·상태표시까지만 책임. | 메일박스 존재 확인됨 / 확인메일 로직은 API | Phase 4만 차단 |
| P4 | **디자인 정본**: Ink & Paper HSL 토큰·`.btn`·`.field`·`.card`·`.rv` 패턴은 `Vinc/site/index.html`(라인 15~397)에 이미 정의됨. 신규 CSS 최소화·재사용. | 확정 | 차단 아님 |
| P5 | **i18n 정본**: `DECK={en:{}, ko:{...}}` + `data-i` innerHTML 스왑(`index.html` 751~808). 한국어 카피는 발주자 저작본(2026-07-12)이 정본. 신규 문구는 EN(마크업 기본값)+KO(덱) 양쪽 필수. | 확정 | 차단 아님 |
| P6 | **하드 불변식 재확인**: (1) 데스크톱 앱은 무로그인·완전 오프라인, (2) 웹 계정/이메일 시스템은 앱과 **분리**·앱이 phone-home 하지 않음, (3) 서버 API는 로컬 볼트를 읽지 않음. 본 작업은 **마케팅 사이트 전용**이며 앱 코드와 무관. | 확정 | 설계 가드레일 |

핵심: **P2가 유일한 외부 차단 요인.** Phase 1~3(마크업·상태·Sign-in 노출)은 API 없이 전량 진행 가능하며, Phase 4에서만 실제 엔드포인트가 필요하다.

---

## 단계 (phased tasks)

### Phase 1 — 뉴스레터 블록 마크업 + 스타일 (제출 비활성)
삽입 위치: `Vinc/site/index.html`의 `#vows` 섹션 종료(라인 713) **다음**, 최종 CTA `.fin`(라인 716) **앞**. 테마 스토리("데이터 말고 소식만")가 프라이버시 서약 직후에 자연스럽게 이어짐.

| 작업 | 담당 | 완료선 |
|------|------|--------|
| `#i-mail` 심볼을 index 심볼 정의부(라인 402~488)에 추가(login/account에서 복사) | **C** | index에 `#i-mail` `<use>`가 렌더됨 |
| `<section id="news" class="wrap rv">` 블록 삽입 — 기존 `.card`/`.vows` 톤의 패널, 헤딩+서브+`.field` 이메일 입력+`.btn primary` 버튼+허니팟+상태 `<p role="status">` | **C** | EN/KO·light/dark·모바일(≤46rem)에서 레이아웃 정상, `.rv→.in` 리빌 동작 |
| 신규 CSS는 `.news`/`.news-form`/`.news-note`만 추가, 색·반경·그림자는 토큰 재사용(신규 색상 하드코딩 0) | **C** | 하드코딩 hex 0개(허니팟 off-screen 제외), 토큰만 사용 |
| 제출 버튼 `disabled` 기본값 + 허니팟 `input[name=company]` off-screen | **C** | 이 단계에선 폼이 아무 요청도 보내지 않음 |

참고 마크업(정본 클래스 준수):
```html
<!-- #vows(713) 다음 · .fin(716) 앞 -->
<section id="news" class="wrap rv">
  <div class="news">
    <span class="news-tag" data-i="newsTag">Release news</span>
    <h2 data-i="newsT">Release news, not your data.</h2>
    <p class="news-sub" data-i="newsS">One short email when a new version ships. No tracking, no marketing lists, unsubscribe in one click.</p>
    <form class="news-form" id="newsForm" novalidate>
      <label class="field">
        <span class="fl"><svg aria-hidden="true"><use href="#i-mail"/></svg></span>
        <input id="newsEmail" type="email" name="email" autocomplete="email"
               placeholder="you@example.com" required aria-label="Email">
      </label>
      <input type="text" name="company" id="newsHp" tabindex="-1" autocomplete="off"
             aria-hidden="true" style="position:absolute;left:-9999px"><!-- 허니팟 -->
      <button class="btn primary" type="submit" id="newsBtn" data-i="newsBtn" disabled>Notify me</button>
    </form>
    <p class="news-note" data-i="newsConsent">Double opt-in — we'll email a link to confirm. The desktop app stays offline and never sees this.</p>
    <p class="news-status" id="newsStatus" role="status" aria-live="polite"></p>
  </div>
</section>
```

### Phase 2 — 폼 상태 로직 + 접근성 (목업/플래그 OFF)
기존 메인 인라인 `<script>`(라인 751~839) 안, 리빌 IO 블록 뒤에 핸들러 추가. **download 페이지의 `deck()` 헬퍼(download `index.html` 335)와 동일한 함수를 index에도 추가**(index엔 없음)해 JS에서 현재 언어 덱을 읽는다.

| 작업 | 담당 | 완료선 |
|------|------|--------|
| `deck()` 헬퍼 index에 추가 + 상태 문자열을 덱 키로 관리 | **C** | JS가 EN/KO 상태 문구를 덱에서 조회 |
| `addEventListener('submit')` 핸들러 — idle/busy/success/error/invalid 상태 처리, 전송 중 버튼 disable(중복 제출 방지) | **C** | 5개 상태 모두 목 resolve로 시연 가능 |
| 클라이언트 검증(`type=email`+정규식) + 허니팟 채워지면 무음 종료 | **C** | 잘못된 이메일 → `newsBad`, 봇 필드 채움 → 요청 없음 |
| 접근성: `<label>`/`aria-label`, `role="status" aria-live="polite"`로 상태 알림, 성공 시 폼 숨김·상태로 포커스 이동 | **C** | 키보드 전용·스크린리더로 상태 전달 확인 |
| 엔드포인트 플래그 `const SUBSCRIBE = null;` — null이면 버튼 disable 유지 + 제출 시 `newsSoon` 안내 | **C** | 플래그 OFF에서 네트워크 요청 0, 안내 문구 노출 |

참고 핸들러(동일 출처 fetch만):
```js
// CSP-safe: 동일 출처 fetch만. API 준비 시 SUBSCRIBE를 "/api/subscribe"로 교체(Phase 4).
function deck(){ return DECK[document.documentElement.lang==="ko"?"ko":"en"] || DECK.en; }
const SUBSCRIBE = null;
const nf = document.getElementById("newsForm");
const nbtn = document.getElementById("newsBtn");
const nstat = document.getElementById("newsStatus");
const nmsg = k => (deck()[k] || DECK.en[k] || "");
if (SUBSCRIBE) nbtn.disabled = false;
nf && nf.addEventListener("submit", async e => {
  e.preventDefault();
  if (document.getElementById("newsHp").value) return;                 // 허니팟
  if (!SUBSCRIBE){ nstat.textContent = nmsg("newsSoon"); return; }
  const email = document.getElementById("newsEmail").value.trim();
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)){ nstat.textContent = nmsg("newsBad"); return; }
  const idle = nbtn.textContent; nbtn.disabled = true; nbtn.textContent = nmsg("newsBusy");
  try {
    const r = await fetch(SUBSCRIBE, { method:"POST",
      headers:{"Content-Type":"application/json"},
      body: JSON.stringify({ email, lang: document.documentElement.lang }) });
    if (r.ok){ nf.hidden = true; nstat.textContent = nmsg("newsOk"); }
    else if (r.status === 409){ nstat.textContent = nmsg("newsDup"); }
    else { nstat.textContent = nmsg("newsErr"); }
  } catch { nstat.textContent = nmsg("newsErr"); }
  finally { nbtn.disabled = false; nbtn.textContent = idle; }
});
```
> 보안 주의: 서버 응답 본문을 그대로 렌더하지 말 것 — 상태 코드만 보고 **로컬 덱 문자열**로 표시(XSS·문구 일관성).

### Phase 3 — Sign-in 노출 반영 (결정 D2 실행)
권고안(아래 결정 참조): **상단 nav엔 Sign-in 없음**, **푸터에 "Web account" 링크**로만 노출.

| 작업 | 담당 | 완료선 |
|------|------|--------|
| index 푸터(`footer .row` 725~731)에 `<a href="login/" data-i="fSignin">Web account</a>` 추가 | **C** | 홈 푸터에 링크 노출·`/login` 이동 |
| 서브페이지 푸터에도 동일 링크(경로 `../login/`): download(283~288)·login(261~267)·account(277~283)·release | **C** | 전 페이지 푸터 문구·경로 일관 |
| 상단 nav(`nav .end` 511~518)는 **변경 없음** — Download 버튼만 유지 | **C** | 히어로 "no sign-up, no login"과 충돌 요소 0 |
| `/login` note 문구가 이미 "the desktop app still needs no account"로 분리 메시지 명시함을 확인·유지(login 249) | **C** | 앱/웹 분리 메시지 회귀 없음 |

### Phase 4 — 실 API 연동 (하드 의존 P2)
| 작업 | 담당 | 완료선 |
|------|------|--------|
| `PLAN-web-account-email`에 **동일 출처 `POST /api/subscribe`** + Caddy `/api/*` 프록시 확정 요청 | **O** | 스테이징에서 `/api/subscribe` 200/409 응답 |
| `SUBSCRIBE = "/api/subscribe"`로 플래그 전환, 버튼 활성화 | **C** | 실제 POST 발생·상태 매핑 동작 |
| 응답 계약 매핑: 200/201=성공(`newsOk`), 409=중복(`newsDup`), 429=레이트리밋(`newsErr` 또는 전용), 5xx/네트워크=`newsErr` | **C** | 각 코드가 올바른 로컬 문구로 표시 |
| E2E: 이메일 입력 → 확인 메일(`vincs@vincs.app`) 수신 → 확인 링크 → `/account` "릴리스 소식" 토글 ON 반영 | **C+O** | 더블 옵트인 왕복 1건 성공 |

### Phase 5 — QA + 배포
| 작업 | 담당 | 완료선 |
|------|------|--------|
| CSP 감사: DevTools Network/Console에 차단 요청·인라인 위반 0 | **C** | 외부 요청 0, 위반 0 |
| 크로스: Chrome/FF/Safari, light/dark, KO/EN, `prefers-reduced-motion`, 모바일 | **C** | 전 조합 정상, 문구 누락(영문 leak) 0 |
| tar-over-ssh 배포 → `/srv/www`, `chown vinc:vinc`, 스모크 테스트 | **O** | `vincs.app`에서 구독·확인 왕복 성공 |

---

## 결정 필요 (owner decisions)

| ID | 결정 | 옵션 | 권고 기본값 & 근거 |
|----|------|------|--------------------|
| D1 | 뉴스레터 블록 위치 | (a) `#vows` 다음·`.fin` 앞 전용 섹션 / (b) 푸터 내부 / (c) `/download`·`/release`에도 | **(a)** — 프라이버시 서약 직후라 "데이터 말고 소식만" 태그라인이 문맥상 강함. 홈 MVP 후 필요 시 (c) 확장 |
| D2 | **Sign-in 노출 위치(핵심)** | (a) 푸터 링크 → `/login` / (b) `/account` 안에서만 / (c) Pro 출시까지 숨김 | **(a) 푸터 전용.** 히어로가 앱을 "무가입·무로그인"으로 강조하므로 상단 nav 로그인 버튼은 **직접 모순**(앱이 로그인 필요하다는 오해). 그러나 릴리스 소식은 **지금** 존재 → 구독자가 해지·관리하려면 사이트에서 로그인 진입점이 반드시 필요. (b)/(c)는 이메일 링크 외 진입로가 없어 과도하게 숨겨짐. 푸터가 "조용하지만 발견 가능"의 균형점 |
| D3 | 푸터 링크 라벨 | "Sign in / 로그인" vs "Web account / 웹 계정" | **"Web account / 웹 계정".** "Sign in"만 두면 앱 로그인으로 오해 가능. "웹 계정"이 앱과의 분리를 라벨 자체로 전달 |
| D4 | 동의 방식 | 필수 체크박스 vs 안내 문구만 | **안내 문구만.** 더블 옵트인(확인 메일 클릭)이 곧 동의 → 마찰 최소화. `newsConsent` 한 줄로 명시 |
| D5 | 성공 UX | 인라인 상태 스왑 vs 리다이렉트 | **인라인 스왑.** 정적 SPA-less 패턴(login "Check your inbox" 프리뷰와 동일 톤) 유지 |
| D6 | 스팸 방지 | 캡차 / 허니팟+서버 레이트리밋 | **허니팟 + 서버 레이트리밋.** 캡차는 외부 로드 필요 → CSP·정적 불변식 위반이자 금지 액션. 봇 방지는 허니팟(클라)+레이트리밋(API) |

---

## 리스크·함정

- **[치명] 교차 출처 엔드포인트**: `/api/subscribe`가 서브도메인/외부면 `default-src 'self'`가 `fetch`를 **차단**. CSP를 완화하면 "정적 전용·최소 표면" 불변식이 깨짐. → API를 반드시 **동일 오리진**에 두고 Caddy `/api/*` reverse_proxy로 해결(P2를 API 플랜에 명문 요청).
- **메시지 혼선(앱=무로그인 vs 웹=Sign in)**: 상단 nav 로그인 배치 시 즉시 모순. → D2 푸터 전용 + D3 "웹 계정" 라벨 + `newsConsent`·`/login` note의 "앱은 계정 불필요" 문구로 분리 유지.
- **phone-home 불변식 오해**: 본 구독은 **마케팅 사이트 전용**. 데스크톱 앱 코드는 이 폼·엔드포인트를 절대 호출하지 않음 — 앱/웹 코드베이스 분리 유지, 앱에 어떤 구독 URL도 심지 말 것.
- **i18n 누락**: 신규 `data-i`(newsTag/newsT/newsS/newsBtn/newsConsent/fSignin)와 JS 상태 키(newsBusy/newsOk/newsErr/newsBad/newsDup/newsSoon)를 KO 덱에 빠뜨리면 한국어에서 영문 leak. → 키 체크리스트로 QA.
- **index엔 `deck()`·`#i-mail` 없음**: download엔 `deck()` 존재하나 index엔 없음, `#i-mail` 심볼은 login/account에만 있음. 복사 누락 시 아이콘 깨짐·JS 참조 에러. → Phase 1·2 작업 항목에 명시.
- **중복 제출·서버 문구 렌더**: 전송 중 버튼 미비활성 → 중복 요청. 서버 에러 본문 직접 렌더 → XSS/문구 불일치. → 버튼 disable + 상태코드→로컬 덱 매핑만.
- **reduced-motion/테마 회귀**: 신규 섹션이 배경 canvas·`.rv` 위에 얹힘. 신규 색 하드코딩·모션 추가 금지, 토큰·`.rv`만 재사용.

---

## 개략 공수 (rough effort)

| Phase | 범위 | 담당 | 공수 |
|-------|------|------|------|
| 0 | 결정 확정(D1~D6) | O | ~0.5d |
| 1 | 블록 마크업+스타일(제출 비활성) | C | ~0.5d |
| 2 | 폼 상태 로직 + 접근성 + i18n(플래그 OFF) | C | ~0.5d |
| 3 | Sign-in 푸터 노출(전 페이지 일관) | C | ~0.25d |
| 4 | 실 `/api/subscribe` 연동 + E2E(API 준비 후) | C+O | ~0.5d |
| 5 | QA(CSP·크로스·KO/EN·모바일) + 배포 | C+O | ~0.5d |
| — | **합계** | — | **~2.75d** (Phase 1~3 ≈1.25d는 API와 독립 병행 가능) |

---

### 부록 — 카피 제안 (EN 마크업 기본값 / KO 덱)

| 키 | EN | KO |
|----|----|----|
| newsTag | Release news | 릴리스 소식 |
| newsT | Release news, not your data. | 당신의 데이터 말고, 릴리스 소식만. |
| newsS | One short email when a new version ships. No tracking, no marketing lists, unsubscribe in one click. | 새 버전이 나올 때만 짧은 메일 한 통. 추적도, 마케팅 목록도 없이 클릭 한 번으로 구독 취소. |
| newsBtn | Notify me | 소식 받기 |
| newsBusy | Sending… | 보내는 중… |
| newsConsent | Double opt-in — we'll email a link to confirm. The desktop app stays offline and never sees this. | 이중 확인(더블 옵트인) — 확인 링크를 메일로 보내드립니다. 데스크톱 앱은 오프라인이며 이 정보를 절대 알지 못합니다. |
| newsOk | Almost there — check your inbox and click the confirmation link. | 거의 다 됐어요 — 메일함에서 확인 링크를 눌러 주세요. |
| newsDup | You're already on the list. | 이미 구독 중입니다. |
| newsBad | Please enter a valid email address. | 올바른 이메일 주소를 입력해 주세요. |
| newsErr | Something went wrong. Please try again in a moment. | 문제가 발생했습니다. 잠시 후 다시 시도해 주세요. |
| newsSoon | Signup opens with the first stable release. Check back soon. | 첫 정식 릴리스와 함께 구독이 열립니다. 곧 다시 확인해 주세요. |
| fSignin | Web account | 웹 계정 |

참조 소스: `Vinc/site/index.html`(토큰 15~35·`.btn` 133~150·`.field`는 login에서 차용·`DECK`/`setLang` 751~808·푸터 725~731·심볼 402~488), `Vinc/site/download/index.html`(`deck()` 335·동일출처 패턴), `Vinc/site/login/index.html`(`#i-mail` 207·매직링크 목업 242~249), `Vinc/site/account/index.html`("릴리스 소식" 토글 256~264).