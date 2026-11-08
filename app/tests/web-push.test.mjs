import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

// 이 기기의 알림(웹 푸시). web-push.ts 는 import 가 없어 그대로 불러온다.
// 서비스 워커(public/sw.js)와 매니페스트는 소스를 읽어 대조한다. 계획: vinc.plan.agent-approvals-in-the-vinc-app-2026-11-07 §4
const read = (p) => readFile(new URL(`../${p}`, import.meta.url), 'utf8');
const js = ts.transpileModule(await read('src/web-push.ts'),
  { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const P = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);

const PIXEL = 'Mozilla/5.0 (Linux; Android 15; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36';
const IPHONE = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1';
const IPAD = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15';
const MAC = IPAD;
const WIN = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36';

test('Android Chrome can subscribe in a tab; iPhone needs the Home Screen app', () => {
  assert.equal(P.platformOf(PIXEL, false), 'android');
  assert.equal(P.platformOf(IPHONE, false), 'ios-browser');
  assert.equal(P.platformOf(IPHONE, true), 'ios-installed');
  assert.equal(P.platformOf(IPAD, false, 5), 'ios-browser');
  assert.equal(P.platformOf(MAC, false, 0), 'other');
  assert.equal(P.platformOf(WIN, false), 'other');
});

test('the state shown follows the device, the server and the permission, in that order', () => {
  const s = (over) => P.pushState({ supported: true, platform: 'android', enabled: true, permission: 'default', subscribed: false, ...over });
  assert.equal(s({ platform: 'ios-browser', supported: false }), 'ios-install');
  assert.equal(s({ supported: false }), 'unsupported');
  assert.equal(s({ enabled: false }), 'unavailable');
  assert.equal(s({ permission: 'denied' }), 'blocked');
  assert.equal(s({}), 'off');
  assert.equal(s({ permission: 'granted' }), 'off');
  assert.equal(s({ permission: 'granted', subscribed: true }), 'on');
  // 권한을 거둔 기기는 구독 행이 남아 있어도 켜짐이 아니다.
  assert.equal(s({ permission: 'default', subscribed: true }), 'off');
});

test('device labels name the browser and the system only', () => {
  assert.equal(P.deviceLabel(PIXEL), 'Android Chrome');
  assert.equal(P.deviceLabel(IPHONE), 'iPhone Safari');
  assert.equal(P.deviceLabel(WIN), 'Windows Chrome');
  assert.equal(P.deviceLabel(''), 'Browser');
});

test('the server key decodes to an uncompressed P-256 point or nothing', () => {
  const point = Buffer.concat([Buffer.from([4]), Buffer.alloc(64, 7)]).toString('base64url');
  const k = P.keyBytes(point);
  assert.equal(k.length, 65);
  assert.equal(k[0], 4);
  assert.equal(P.keyBytes(point.slice(0, 40)), null);
  assert.equal(P.keyBytes('not base64!'), null);
  assert.equal(P.sameKey(k.buffer, k), true);
  assert.equal(P.sameKey(new Uint8Array(65).buffer, k), false);
  assert.equal(P.sameKey(null, k), false);
});

test('the subscribe body carries the endpoint, the two keys and a label, nothing else', () => {
  const sub = { endpoint: 'https://fcm.googleapis.com/fcm/send/x', expirationTime: null, keys: { p256dh: 'p', auth: 'a', extra: 'z' } };
  assert.deepEqual(P.subscriptionBody(sub, 'Android Chrome'),
    { endpoint: 'https://fcm.googleapis.com/fcm/send/x', keys: { p256dh: 'p', auth: 'a' }, label: 'Android Chrome' });
  assert.equal(P.subscriptionBody({ endpoint: 'http://x', keys: { p256dh: 'p', auth: 'a' } }, ''), null);
  assert.equal(P.subscriptionBody({ endpoint: 'https://x', keys: { p256dh: 'p' } }, ''), null);
  assert.equal(P.subscriptionBody(null, ''), null);
});

test('a notification opens only a page inside the app', () => {
  const o = 'https://vincs.io';
  assert.equal(P.openTarget('/app/approvals/episode%3Arun-a', o, '/app/'), 'https://vincs.io/app/approvals/episode%3Arun-a');
  assert.equal(P.openTarget('/app/approvals/x?space=t', o, '/app/'), 'https://vincs.io/app/approvals/x?space=t');
  assert.equal(P.openTarget('/app', o, '/app/'), 'https://vincs.io/app');
  for (const bad of ['https://evil.example/app/', '//evil.example/app/', '/account/', '/apple', 'javascript:alert(1)', 42, null])
    assert.equal(P.openTarget(bad, o, '/app/'), 'https://vincs.io/app/');
});

test('the service worker shows the copy, never asks on its own, and opens inside the app', async () => {
  const sw = await read('public/sw.js');
  const copy = await read('src/copy.ts');
  const en = /push: \{[\s\S]*?body: "([^"]+)"/.exec(copy)[1];
  assert.equal(/FALLBACK_BODY = "([^"]+)"/.exec(sw)[1], en);
  assert.doesNotMatch(sw, /requestPermission|pushManager\.subscribe/);
  assert.match(sw, /addEventListener\("push"/);
  assert.match(sw, /addEventListener\("notificationclick"/);
  // 알림 본문에 행동·질문 자리가 없다: 본문의 title·run·url 만 읽는다.
  const reads = [...sw.matchAll(/(?<![.\w])data\.(\w+)/g)].map((m) => m[1]);
  assert.deepEqual([...new Set(reads)].sort(), ['run', 'title', 'url']);
});

test('the app asks for permission only from the button, and registers only on the web', async () => {
  const ui = await read('src/web-push-ui.ts');
  const main = await read('src/main.ts');
  const calls = [...ui.matchAll(/requestPermission\(/g)];
  assert.equal(calls.length, 1);
  const turnOn = ui.slice(ui.indexOf('async function turnOn'), ui.indexOf('async function turnOff'));
  assert.match(turnOn, /requestPermission\(/);
  assert.match(ui, /export function registerWebPush\(\): void \{\n  if \(!WEB\) return;/);
  assert.doesNotMatch(ui.slice(ui.indexOf('export function registerWebPush'), ui.indexOf('async function sha256Hex')), /requestPermission/);
  assert.match(main, /registerWebPush\(\);/);
});

test('the manifest makes Vinc installable from the web app', async () => {
  const m = JSON.parse(await read('public/manifest.webmanifest'));
  assert.equal(m.name, 'Vinc');
  assert.equal(m.display, 'standalone');
  assert.equal(m.start_url, '/app/');
  assert.ok(m.icons.some((i) => i.sizes === '512x512' && i.type === 'image/png'));
});
