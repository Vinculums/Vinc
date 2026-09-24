// 로고 래스터와 배포용 임베드를 정본 SVG에서 재생성한다. --check는 쓰지 않는다.
import { readFileSync, writeFileSync, readdirSync, mkdtempSync, rmSync } from 'node:fs';
import { dirname, resolve, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { tmpdir } from 'node:os';
import { execFileSync } from 'node:child_process';
import { Resvg } from '@resvg/resvg-js';

const hub = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const repo = resolve(hub, '..');
const assets = join(hub, 'assets/logo');
const check = process.argv.includes('--check');
let drift = 0;
// 정본에 컬러를 다시 넣으면 래스터와 배포본까지 번지기 전에 중단한다.
const mono = readFileSync(join(assets, 'vinc-ring-mono.svg'), 'utf8');
const geometry = mono.match(/ d="([^"]+)"/)[1];
for (const name of readdirSync(assets).filter(name => name.endsWith('.svg'))) {
  const svg = readFileSync(join(assets, name), 'utf8');
  const fills = [...svg.matchAll(/fill="([^"]+)"/g)].map(match => match[1]);
  if (!fills.length || fills.some(fill => !['#000000', '#FFFFFF', 'currentColor'].includes(fill)) ||
      /Gradient|stop-color|opacity=|style=/.test(svg) || !svg.includes(`d="${geometry}"`)) {
    throw new Error(`Non-monochrome or changed logo geometry: ${name}`);
  }
}
function emit(path, bytes) {
  const actual = Buffer.isBuffer(bytes) ? bytes : Buffer.from(bytes);
  let matches = false;
  try {
    const prior = readFileSync(path);
    matches = path.endsWith('.py')
      ? prior.toString('utf8').replace(/\r\n/g, '\n') === actual.toString('utf8')
      : prior.equals(actual);
  } catch { /* 새 산출물 */ }
  if (matches) return;
  if (check) { console.error(`DRIFT ${path}`); drift++; }
  else { writeFileSync(path, actual); console.log(`Generated ${path}`); }
}
function png(svg, size, mode = 'width') {
  return new Resvg(svg, { fitTo: { mode, value: size } }).render().asPng();
}
// Tauri의 ICNS 엔트리 순서는 실행마다 달라질 수 있어 타입순으로 직렬화한다.
function orderedIcns(bytes) {
  const chunks = [];
  for (let at = 8; at < bytes.length;) {
    const length = bytes.readUInt32BE(at + 4);
    if (length < 8 || at + length > bytes.length) throw new Error('Invalid ICNS chunk');
    chunks.push(bytes.subarray(at, at + length));
    at += length;
  }
  chunks.sort((a, b) => Buffer.compare(a.subarray(0, 4), b.subarray(0, 4)));
  return Buffer.concat([bytes.subarray(0, 8), ...chunks]);
}
const circle = readFileSync(join(assets, 'vinc-ring-circle.svg'), 'utf8');
const white = readFileSync(join(assets, 'vinc-ring-white.svg'), 'utf8');
const black = readFileSync(join(assets, 'vinc-ring-black.svg'), 'utf8');
emit(join(assets, 'vinc-ring-white-email@2x.png'), png(white, 64, 'height'));
// 밝은 메일 템플릿의 잉크판. 흰 마크와 같은 46x64 로 뽑는다.
emit(join(assets, 'vinc-ring-ink-email@2x.png'), png(black, 64, 'height'));
emit(join(assets, 'vinc-ring-favicon-48.png'), png(circle, 48));
const sizes = [16, 32, 48];
const images = sizes.map(size => png(circle, size));
const header = Buffer.alloc(6 + sizes.length * 16);
header.writeUInt16LE(1, 2); header.writeUInt16LE(sizes.length, 4);
let offset = header.length;
sizes.forEach((size, i) => {
  const at = 6 + i * 16;
  header[at] = size; header[at + 1] = size;
  header.writeUInt16LE(1, at + 4); header.writeUInt16LE(32, at + 6);
  header.writeUInt32LE(images[i].length, at + 8); header.writeUInt32LE(offset, at + 12);
  offset += images[i].length;
});
emit(join(assets, 'vinc-ring-favicon.ico'), Buffer.concat([header, ...images]));

// Tauri의 실제 아이콘 생성기를 쓰되 모바일 미사용 산출물은 리포에 추가하지 않는다.
const temporary = mkdtempSync(join(tmpdir(), 'vinc-logo-'));
try {
  execFileSync(process.execPath, [join(repo, 'app/node_modules/@tauri-apps/cli/tauri.js'),
    'icon', join(assets, 'vinc-ring.svg'), '--output', temporary], { stdio: 'pipe' });
  const native = join(repo, 'app/src-tauri/icons');
  for (const name of readdirSync(native)) {
    if (!/\.(png|ico|icns)$/.test(name)) continue;
    const bytes = readFileSync(join(temporary, name));
    emit(join(native, name), name.endsWith('.icns') ? orderedIcns(bytes) : bytes);
  }
  const raster = readFileSync(join(temporary, '128x128.png'));
  const serverPath = join(repo, 'vinc_mcp/http_server.py');
  let server = readFileSync(serverPath, 'utf8').replace(/\r\n/g, '\n');
  const pngPattern = /_FAVICON_PNG = base64\.b64decode\([\s\S]*?\r?\n\)/;
  const svgPattern = /_FAVICON_SVG = """[\s\S]*?"""/;
  if (!pngPattern.test(server) || !svgPattern.test(server)) throw new Error('MCP favicon constants not found');
  server = server.replace(pngPattern, `_FAVICON_PNG = base64.b64decode(\n    "${raster.toString('base64')}"\n)`)
    .replace(svgPattern, `_FAVICON_SVG = """${circle.trim()}"""`);
  emit(serverPath, server);
} finally { rmSync(temporary, { recursive: true, force: true }); }
if (drift) process.exitCode = 1;
else console.log(check ? 'Logo generated assets match canonical SVGs' : 'Logo generation complete; run sync_out.py --apply');
