// Vinc 웹 앱의 서비스 워커(웹 푸시). 웹 빌드에서만 등록한다(`src/web-push-ui.ts`, `WEB` 일 때).
// 데스크톱 셸 빌드에도 이 파일이 실리지만 아무도 등록하지 않는다. 캐시·오프라인은 하지 않는다.
//
// push: 본문 {title, run, url} 로 알림 하나를 띄운다. 알림 문장은 앱이 Cache Storage 에 넣어 둔 그 사람의
//   언어의 문구(copy.ts `push.body`)이고, 없으면 아래 영어 문장이다. 행동·질문은 본문에 없다.
// notificationclick: 열린 Vinc 창이 있으면 그 창을 그 주소로 옮겨 앞으로 가져오고, 없으면 새 창을 연다.
//   주소는 이 앱 안(같은 출처, 범위 /app/ 아래)일 때만 따르고, 아니면 앱 첫 화면을 연다.
"use strict";

const COPY_CACHE = "vinc-push-copy-v1";
// copy.ts EN `push.body` 와 같아야 한다(tests/web-push.test.mjs 가 대조한다).
const FALLBACK_BODY = "An agent is waiting for your answer. Tap to open it.";
const SCOPE = new URL(self.registration.scope);

function target(raw) {
  try {
    const u = new URL(typeof raw === "string" ? raw : "", self.location.origin);
    if (u.origin === self.location.origin && (u.pathname + "/").startsWith(SCOPE.pathname)) return u.href;
  } catch (_) { /* 아래 기본값 */ }
  return SCOPE.href;
}

async function bodyText() {
  try {
    const cache = await caches.open(COPY_CACHE);
    const hit = await cache.match(new URL("push-copy.json", SCOPE).href);
    if (hit) {
      const copy = await hit.json();
      if (copy && typeof copy.body === "string" && copy.body) return copy.body;
    }
  } catch (_) { /* 영어로 */ }
  return FALLBACK_BODY;
}

self.addEventListener("install", () => { self.skipWaiting(); });
self.addEventListener("activate", (event) => { event.waitUntil(self.clients.claim()); });

self.addEventListener("push", (event) => {
  event.waitUntil((async () => {
    let data = {};
    try { data = event.data ? event.data.json() : {}; } catch (_) { data = {}; }
    const title = typeof data.title === "string" && data.title ? data.title.slice(0, 120) : "Vinc";
    const run = typeof data.run === "string" ? data.run : "";
    await self.registration.showNotification(title, {
      body: await bodyText(),
      // 같은 실행의 알림은 하나로 바꿔 쓴다. 다시 물으면 다시 울린다.
      tag: run ? "run:" + run : "vinc-test",
      renotify: Boolean(run),
      icon: new URL("icons/vinc-256.png", SCOPE).href,
      data: { url: target(data.url) },
    });
  })());
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = target(event.notification.data && event.notification.data.url);
  event.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const w of wins) {
      if (!(new URL(w.url).pathname + "/").startsWith(SCOPE.pathname)) continue;
      // 앞으로 가져오기는 누른 그 순간에 부른다(브라우저가 그때만 허락한다). 실패해도 옮기기는 한다.
      const focusing = w.focus().catch(() => null);
      let moved = null;
      try { moved = await w.navigate(url); } catch (_) { /* 이 워커가 다스리지 않는 창은 옮기지 못한다 */ }
      if (!moved) w.postMessage({ type: "vinc:open", url });
      await focusing;
      return;
    }
    await self.clients.openWindow(url);
  })());
});
