/** 이 기기의 알림(웹 푸시): 서비스 워커 등록과 설정의 "이 기기의 알림" 칸.
 *
 *  - 웹 Vinc 에서만 쓴다(`WEB`). 데스크톱 셸은 서비스 워커도 매니페스트도 등록하지 않는다.
 *  - 권한은 사람이 "알림 켜기"를 누를 때만 묻는다. 부팅·설정 열기에서는 묻지 않는다.
 *  - 구독은 계정(`/api/push/*`, 쿠키 세션 전용)에 맡긴다. 끄면 브라우저 구독도 지우고 계정 행도 지운다.
 *  판정은 `web-push.ts`(순수 함수, 노드 테스트)에 있다. */
import { api, WEB } from "./api";
import { COPY } from "./copy";
import { esc } from "./components";
import { deviceLabel, keyBytes, openTarget, platformOf, pushState, sameKey, subscriptionBody,
         type PushState } from "./web-push";

const COPY_CACHE = "vinc-push-copy-v1";
const base = () => new URL(import.meta.env.BASE_URL || "/", location.origin);

function supported(): boolean {
  return "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
}

/** 웹 부팅에서 한 번: 매니페스트를 걸고 서비스 워커를 등록한다. 권한은 묻지 않는다. */
export function registerWebPush(): void {
  if (!WEB) return;
  const root = base();
  if (!document.querySelector('link[rel="manifest"]')) {
    const link = document.createElement("link");
    link.rel = "manifest";
    link.href = new URL("manifest.webmanifest", root).pathname;
    document.head.appendChild(link);
  }
  if (!("serviceWorker" in navigator)) return;
  void navigator.serviceWorker.register(new URL("sw.js", root).pathname, { scope: root.pathname })
    .catch(() => { /* 알림만 못 쓴다. 화면은 그대로 */ });
  // 알림의 문장은 그 사람의 언어로. 서비스 워커는 앱의 문구를 못 읽으므로 여기서 넣어 둔다.
  if ("caches" in window) {
    void caches.open(COPY_CACHE)
      .then((c) => c.put(new URL("push-copy.json", root).href,
        new Response(JSON.stringify({ body: COPY.push.body }), { headers: { "Content-Type": "application/json" } })))
      .catch(() => {});
  }
  // 서비스 워커가 다스리지 않는 창은 워커가 옮기지 못한다. 그때 워커가 이 창에 주소를 보낸다.
  navigator.serviceWorker.addEventListener("message", (e) => {
    const d = e.data as { type?: string; url?: unknown } | null;
    if (d?.type === "vinc:open") location.assign(openTarget(d.url, location.origin, root.pathname));
  });
}

async function sha256Hex(text: string): Promise<string> {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

type Device = { id: number; label: string; last_error: string | null; endpoint_sha256: string };

/** 설정의 "이 기기의 알림" 칸을 그린다. 떠날 때 부를 정리 함수를 돌려준다. */
export function mountPushSettings(box: HTMLElement): () => void {
  const N = COPY.push;
  let alive = true;
  const platform = platformOf(navigator.userAgent, matchMedia("(display-mode: standalone)").matches,
                              navigator.maxTouchPoints || 0);

  async function current(): Promise<PushSubscription | null> {
    if (!supported()) return null;
    const reg = await navigator.serviceWorker.getRegistration(base().pathname);
    return reg ? reg.pushManager.getSubscription() : null;
  }

  async function draw(note = "") {
    if (!alive) return;
    let enabled = false, publicKey = "", devices: Device[] = [];
    if (supported() && platform !== "ios-browser") {
      try {
        const r = await api.pushDevices();
        const d = r.ok ? await r.json() : null;
        enabled = !!d?.enabled;
        publicKey = d?.public_key || "";
        devices = Array.isArray(d?.devices) ? d.devices : [];
      } catch { enabled = false; }
    }
    const sub = await current().catch(() => null);
    const mineHash = sub ? await sha256Hex(sub.endpoint) : "";
    const mine = !!sub && devices.some((x) => x.endpoint_sha256 === mineHash);
    const state: PushState = pushState({ supported: supported(), platform, enabled,
      permission: supported() ? Notification.permission : "default", subscribed: mine });
    if (!alive) return;
    const others = devices.filter((x) => x.endpoint_sha256 !== mineHash);
    const msg: Record<PushState, string> = { unsupported: N.unsupported, "ios-install": N.iosInstall,
      unavailable: N.unavailable, blocked: N.blocked, off: N.off, on: N.on };
    box.innerHTML = `
      <p class="hint">${esc(N.hint)}</p>
      <p id="push-state" role="status" aria-live="polite">${esc(msg[state])}</p>
      ${state === "off" ? `<p><button class="btn" id="push-on" type="button">${esc(N.turnOn)}</button></p>` : ""}
      ${state === "on" ? `<p><button class="btn" id="push-test" type="button">${esc(N.sendTest)}</button>
        <button class="btn" id="push-off" type="button" style="margin-left:.5rem">${esc(N.turnOff)}</button></p>` : ""}
      ${state === "on" && devices.find((x) => x.endpoint_sha256 === mineHash)?.last_error
        ? `<p class="hint">${esc(N.lastFailed)}</p>` : ""}
      ${others.length && enabled ? `<p class="hint">${esc(N.devices)}</p><ul class="push-devices">${others.map((x) =>
        `<li>${esc(x.label || deviceLabel(""))} <button class="btn" type="button" data-push-remove="${x.id}">${
          esc(N.remove)}</button></li>`).join("")}</ul>` : ""}
      ${note ? `<p class="hint" id="push-note" role="status">${esc(note)}</p>` : ""}`;
    const busy = (b: HTMLButtonElement, run: () => Promise<string | void>) => b.addEventListener("click", async () => {
      box.querySelectorAll<HTMLButtonElement>("button").forEach((x) => { x.disabled = true; });
      const state = box.querySelector<HTMLElement>("#push-state");
      if (state) state.textContent = N.working;
      let after = "";
      try { after = (await run()) || ""; } catch { after = N.failed; }
      void draw(after);
    });
    const on = box.querySelector<HTMLButtonElement>("#push-on");
    if (on) busy(on, () => turnOn(publicKey));
    const off = box.querySelector<HTMLButtonElement>("#push-off");
    if (off) busy(off, turnOff);
    const test = box.querySelector<HTMLButtonElement>("#push-test");
    if (test) busy(test, async () => {
      const r = await api.pushPost("test", { title: N.testTitle });
      const d = r.ok ? await r.json() : null;
      if (!d) return N.failed;
      return d.sent ? N.testSent : N.testNone;
    });
    box.querySelectorAll<HTMLButtonElement>("[data-push-remove]").forEach((b) => busy(b, async () => {
      const r = await api.pushPost("unsubscribe", { id: Number(b.dataset.pushRemove) });
      return r.ok ? "" : N.failed;
    }));
  }

  async function turnOn(publicKey: string): Promise<string | void> {
    const key = keyBytes(publicKey);
    if (!key) return N.unavailable;
    // 사람이 버튼을 누른 이 자리에서만 권한을 묻는다.
    const permission = await Notification.requestPermission();
    if (permission !== "granted") return;   // 거절하면 막힘으로, 닫기만 했으면 꺼짐으로 다시 그린다
    const root = base();
    await navigator.serviceWorker.register(new URL("sw.js", root).pathname, { scope: root.pathname });
    const reg = await navigator.serviceWorker.ready;
    let sub = await reg.pushManager.getSubscription();
    if (sub && !sameKey(sub.options.applicationServerKey, key)) { await sub.unsubscribe(); sub = null; }
    sub ??= await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key as BufferSource });
    const body = subscriptionBody(sub.toJSON() as { endpoint?: string; keys?: Record<string, string> },
                                  deviceLabel(navigator.userAgent, navigator.maxTouchPoints || 0));
    if (!body) return N.failed;
    const r = await api.pushPost("subscribe", body);
    if (!r.ok) { await sub.unsubscribe().catch(() => false); return N.failed; }
  }

  async function turnOff(): Promise<string | void> {
    const sub = await current();
    if (!sub) return;
    const endpoint = sub.endpoint;
    await sub.unsubscribe().catch(() => false);
    const r = await api.pushPost("unsubscribe", { endpoint });
    if (!r.ok) return N.failed;
  }

  void draw();
  return () => { alive = false; };
}
