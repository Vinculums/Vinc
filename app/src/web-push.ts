/** 이 기기의 알림(웹 푸시)의 판정. 화면(`web-push-ui.ts`)이 쓰는 순수 함수만 둔다.
 *
 *  알림은 웹 Vinc 에서만 켠다(데스크톱 셸에는 서비스 워커를 등록하지 않는다). 권한은 사람이 버튼을 누를 때만
 *  묻는다: 페이지를 열 때 묻지 않는다. Android Chrome 은 열린 탭에서 바로 구독할 수 있고, iPhone 은 홈 화면에
 *  추가한 Vinc 안에서만 된다(iOS 16.4 이상). 푸시 본문은 `{title, run, url}` 셋뿐이고 행동은 실리지 않는다.
 *  계획: `vinc.plan.agent-approvals-in-the-vinc-app-2026-11-07` §4
 *
 *  import 가 없다: 노드 테스트가 이 파일을 그대로 불러 쓴다(`tests/web-push.test.mjs`). */

export type PushPlatform = "android" | "ios-browser" | "ios-installed" | "other";
export type PushState = "unsupported" | "ios-install" | "unavailable" | "blocked" | "off" | "on";

/** 아이폰·아이패드인가. iPadOS 의 Safari 는 맥으로 보이므로 터치 점 수로 가른다. */
function isIos(ua: string, touchPoints: number): boolean {
  return /iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && touchPoints > 1);
}

/** 이 기기의 푸시 사정. `standalone` 은 홈 화면에서 연 창인가(`display-mode: standalone`). */
export function platformOf(ua: string, standalone: boolean, touchPoints = 0): PushPlatform {
  if (isIos(ua, touchPoints)) return standalone ? "ios-installed" : "ios-browser";
  if (/Android/.test(ua)) return "android";
  return "other";
}

/** 기기 목록에 보일 이름. 사람이 고르지 않으므로 브라우저와 운영체제만 적는다(UA 원문은 보내지 않는다). */
export function deviceLabel(ua: string, touchPoints = 0): string {
  const browser = /EdgA?\//.test(ua) ? "Edge" : /SamsungBrowser\//.test(ua) ? "Samsung Internet"
    : /Firefox\/|FxiOS\//.test(ua) ? "Firefox" : /OPR\//.test(ua) ? "Opera"
    : /Chrome\/|CriOS\//.test(ua) ? "Chrome" : /Safari\//.test(ua) ? "Safari" : "Browser";
  const os = /Android/.test(ua) ? "Android" : isIos(ua, touchPoints) ? (/iPad/.test(ua) || /Macintosh/.test(ua) ? "iPad" : "iPhone")
    : /Windows/.test(ua) ? "Windows" : /Mac OS X|Macintosh/.test(ua) ? "Mac" : /CrOS/.test(ua) ? "ChromeOS"
    : /Linux/.test(ua) ? "Linux" : "";
  return os ? `${os} ${browser}` : browser;
}

/** VAPID 공개키(base64url) → `applicationServerKey` 바이트. 모양이 틀리면 null. */
export function keyBytes(b64u: string): Uint8Array | null {
  if (typeof b64u !== "string" || !/^[A-Za-z0-9_-]+={0,2}$/.test(b64u)) return null;
  const s = b64u.replace(/-/g, "+").replace(/_/g, "/");
  let bin = "";
  try { bin = atob(s + "=".repeat((4 - (s.length % 4)) % 4)); } catch { return null; }
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out.length === 65 && out[0] === 4 ? out : null;
}

/** 브라우저 구독(`PushSubscription.toJSON()`) → 계정에 보낼 본문. 모자라면 null. */
export function subscriptionBody(sub: { endpoint?: string; keys?: Record<string, string> } | null | undefined,
                                 label: string): { endpoint: string; keys: { p256dh: string; auth: string }; label: string } | null {
  const endpoint = sub?.endpoint, p256dh = sub?.keys?.p256dh, auth = sub?.keys?.auth;
  if (typeof endpoint !== "string" || !endpoint.startsWith("https://") || !p256dh || !auth) return null;
  return { endpoint, keys: { p256dh, auth }, label };
}

/** 지금 화면에 보일 상태. 앞의 것이 이긴다: 못 하는 기기, 아이폰 설치 전, 서버 꺼짐, 막힘, 꺼짐, 켜짐. */
export function pushState(s: { supported: boolean; platform: PushPlatform; enabled: boolean;
                               permission: string; subscribed: boolean }): PushState {
  if (s.platform === "ios-browser") return "ios-install";
  if (!s.supported) return "unsupported";
  if (!s.enabled) return "unavailable";
  if (s.permission === "denied") return "blocked";
  return s.subscribed && s.permission === "granted" ? "on" : "off";
}

/** 구독이 지금 서버 키로 만든 것인가. 키가 바뀌었으면 다시 구독해야 푸시 서비스가 받는다. */
export function sameKey(current: ArrayBuffer | null | undefined, want: Uint8Array): boolean {
  if (!current) return false;
  const a = new Uint8Array(current);
  return a.length === want.length && a.every((v, i) => v === want[i]);
}

/** 서비스 워커가 알림에서 여는 주소가 이 앱 안인가(다른 출처나 앱 밖 경로로는 열지 않는다). */
export function openTarget(raw: unknown, origin: string, scopePath: string): string {
  const root = scopePath.endsWith("/") ? scopePath : scopePath + "/";
  try {
    const u = new URL(typeof raw === "string" ? raw : "", origin);
    if (u.origin === origin && (u.pathname + "/").startsWith(root)) return u.href;
  } catch { /* 아래 기본값 */ }
  return new URL(root, origin).href;
}
