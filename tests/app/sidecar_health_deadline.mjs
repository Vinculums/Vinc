import { readFileSync } from 'node:fs';
import { createServer } from 'node:http';
import { runInNewContext } from 'node:vm';
import { strict as assert } from 'node:assert';
import { test } from 'node:test';

// 실제 클라이언트 함수만 실행한다. DOM·Tauri 없이 HTTP의 헤더/본문 중단을 확인한다.
const api = readFileSync(process.env.VINC_HEALTH_TEST_SOURCE ||
  new URL('../../app/src/api.ts', import.meta.url), 'utf8').replace(/\r\n/g, '\n');
const start = api.indexOf('async function waitForSidecar(');
const end = api.indexOf('\n}\n', start) + 2;
const functionSource = api.slice(start, end)
  .replace('tries: number', 'tries').replace(': Promise<boolean>', '').replace(' as any', '');

async function withServer(handler, verify) {
  const server = createServer(handler);
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const context = { cfg: { base: `http://127.0.0.1:${server.address().port}` },
    fetch, URL, AbortSignal, performance, setTimeout, sidecarDiag: '', startIndexWatch() {} };
  try { await verify(runInNewContext(`(${functionSource})`, context), context); }
  finally { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
}

for (const bodyStarted of [false, true]) {
  test(`bounded health wait when ${bodyStarted ? 'body' : 'headers'} never finish`, async () => {
    await withServer((req, res) => {
      if (bodyStarted) { res.writeHead(200, { 'Content-Type': 'application/json' }); res.write('{'); }
    }, async wait => {
      const started = performance.now();
      const result = await Promise.race([wait(1), new Promise((_, reject) =>
        setTimeout(() => reject(new Error('health wait exceeded its deadline')), 1500))]);
      assert.equal(result, false);
      assert.ok(performance.now() - started < 1250);
    });
  });
}

test('startup 503 can recover within the deadline and preserve health diagnostics', async () => {
  let calls = 0;
  await withServer((req, res) => {
    res.writeHead(++calls === 1 ? 503 : 200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ status: 'degraded', provision_error: 'fixture provisioning' }));
  }, async (wait, context) => {
    assert.equal(await wait(8), true);
    assert.equal(calls, 2);
    assert.match(context.sidecarDiag, /DEGRADED\[fixture provisioning\]/);
  });
});
