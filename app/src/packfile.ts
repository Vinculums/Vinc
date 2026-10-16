/** `.vincpack` 컨테이너 입출력 (D55) — 사이드카 `api/packfile.py` 와 같은 계약.
 *
 *  **왜 손으로 zip 을 쓰는가.** 이 앱에는 zip 라이브러리가 없고 CSP 가 외부 CDN 을
 *  막는다(`default-src 'self'`). 그런데 zip 의 최소 골격은 작고(로컬 헤더 30바이트 +
 *  중앙 디렉터리 46바이트 + EOCD 22바이트), 압축은 브라우저 네이티브
 *  `CompressionStream("deflate-raw")` 가 해 준다 — zip 이 요구하는 raw DEFLATE 가
 *  바로 그것이다. 의존성 0 이라는 이 코드베이스의 규율을 지키면서 표준 컨테이너를
 *  얻는다.
 *
 *  **하위호환이 핵심 계약이다.** 공식 팩 행과 커뮤니티 팩 공유에 구 JSON 팩이 저장돼 있으므로
 *  **읽기는 두 형식 모두**, **쓰기만** zip. 판별은 첫 바이트: `PK` = zip, 그 외 = JSON.
 *
 *  레이아웃(파이썬과 동일):
 *    manifest.json · fragment.json · documents/0001.json …
 */

const MANIFEST = "manifest.json";
const FRAGMENT = "fragment.json";
const DOC_DIR = "documents/";

const te = new TextEncoder();
const td = new TextDecoder();

/** CRC-32 (zip 필수) — 테이블은 첫 호출에 한 번만 만든다. */
let crcTable: Uint32Array | null = null;
function crc32(buf: Uint8Array): number {
  if (!crcTable) {
    crcTable = new Uint32Array(256);
    for (let i = 0; i < 256; i++) {
      let c = i;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      crcTable[i] = c >>> 0;
    }
  }
  let c = 0xffffffff;
  for (let i = 0; i < buf.length; i++) c = crcTable[(c ^ buf[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

async function pipe(data: Uint8Array, s: CompressionStream | DecompressionStream) {
  const out = new Blob([data as BlobPart]).stream().pipeThrough(s as ReadableWritablePair);
  return new Uint8Array(await new Response(out).arrayBuffer());
}
const deflateRaw = (d: Uint8Array) => pipe(d, new CompressionStream("deflate-raw"));
const inflateRaw = (d: Uint8Array) => pipe(d, new DecompressionStream("deflate-raw"));

const isZip = (d: Uint8Array) => d[0] === 0x50 && d[1] === 0x4b;   // "PK"

// ── 쓰기 ────────────────────────────────────────────────────────────────────
type Entry = { name: Uint8Array; crc: number; raw: number; comp: Uint8Array; off: number };

/** 팩 → zip 바이트. 문서는 건별 파일로 흩는다. 헤더 시각은 **고정**(1980-01-01)이라
 *  같은 팩이면 같은 바이트가 나온다(파이썬 쪽과 같은 결정론 규율). */
export async function toBytes(pack: Record<string, unknown>): Promise<Uint8Array> {
  const body: Record<string, unknown> = { ...pack };
  const documents = (body.documents as unknown[]) || [];
  const fragment = body.fragment || { nodes: [], edges: [] };
  delete body.documents; delete body.fragment; delete body.executed;

  const files: Array<[string, unknown]> = [[MANIFEST, body], [FRAGMENT, fragment]];
  documents.forEach((d, i) => files.push([`${DOC_DIR}${String(i + 1).padStart(4, "0")}.json`, d]));

  const parts: Uint8Array[] = [];
  const entries: Entry[] = [];
  let off = 0;
  for (const [name, obj] of files) {
    const raw = te.encode(JSON.stringify(obj, null, 2));
    const comp = await deflateRaw(raw);
    const nm = te.encode(name);
    const h = new DataView(new ArrayBuffer(30));
    h.setUint32(0, 0x04034b50, true);      // 로컬 파일 헤더 시그니처
    h.setUint16(4, 20, true);              // 필요 버전 2.0(deflate)
    h.setUint16(6, 0x800, true);           // 플래그: 파일명 UTF-8
    h.setUint16(8, 8, true);               // 압축 = deflate
    h.setUint16(10, 0, true); h.setUint16(12, 33, true);   // 시각 0 · 날짜 1980-01-01
    h.setUint32(14, crc32(raw), true);
    h.setUint32(18, comp.length, true);
    h.setUint32(22, raw.length, true);
    h.setUint16(26, nm.length, true); h.setUint16(28, 0, true);
    entries.push({ name: nm, crc: crc32(raw), raw: raw.length, comp, off });
    parts.push(new Uint8Array(h.buffer), nm, comp);
    off += 30 + nm.length + comp.length;
  }
  const cdStart = off;
  for (const e of entries) {
    const c = new DataView(new ArrayBuffer(46));
    c.setUint32(0, 0x02014b50, true);      // 중앙 디렉터리 시그니처
    c.setUint16(4, 20, true); c.setUint16(6, 20, true);
    c.setUint16(8, 0x800, true); c.setUint16(10, 8, true);
    c.setUint16(12, 0, true); c.setUint16(14, 33, true);
    c.setUint32(16, e.crc, true);
    c.setUint32(20, e.comp.length, true);
    c.setUint32(24, e.raw, true);
    c.setUint16(28, e.name.length, true);
    c.setUint32(42, e.off, true);
    parts.push(new Uint8Array(c.buffer), e.name);
    off += 46 + e.name.length;
  }
  const eocd = new DataView(new ArrayBuffer(22));
  eocd.setUint32(0, 0x06054b50, true);
  eocd.setUint16(8, entries.length, true);
  eocd.setUint16(10, entries.length, true);
  eocd.setUint32(12, off - cdStart, true);
  eocd.setUint32(16, cdStart, true);
  parts.push(new Uint8Array(eocd.buffer));

  const total = parts.reduce((a, p) => a + p.length, 0);
  const out = new Uint8Array(total);
  let at = 0;
  for (const p of parts) { out.set(p, at); at += p.length; }
  return out;
}

// ── 읽기 ────────────────────────────────────────────────────────────────────
/** 중앙 디렉터리를 훑어 {이름 → 압축해제 바이트}. EOCD 를 뒤에서 찾는다(주석 대비). */
async function unzip(data: Uint8Array): Promise<Map<string, Uint8Array>> {
  const dv = new DataView(data.buffer, data.byteOffset, data.byteLength);
  let eocd = -1;
  for (let i = data.length - 22; i >= 0 && i > data.length - 22 - 0xffff; i--) {
    if (dv.getUint32(i, true) === 0x06054b50) { eocd = i; break; }
  }
  if (eocd < 0) throw new Error("not a zip: EOCD missing");
  const count = dv.getUint16(eocd + 8, true);
  let p = dv.getUint32(eocd + 16, true);
  const out = new Map<string, Uint8Array>();
  for (let i = 0; i < count; i++) {
    if (dv.getUint32(p, true) !== 0x02014b50) throw new Error("zip: bad central header");
    const method = dv.getUint16(p + 10, true);
    const csize = dv.getUint32(p + 20, true);
    const nlen = dv.getUint16(p + 28, true);
    const elen = dv.getUint16(p + 30, true);
    const clen = dv.getUint16(p + 32, true);
    const lho = dv.getUint32(p + 42, true);
    const name = td.decode(data.subarray(p + 46, p + 46 + nlen));
    // 로컬 헤더의 가변 길이는 중앙 디렉터리와 다를 수 있어 **로컬에서 다시 읽는다**.
    const lnlen = dv.getUint16(lho + 26, true);
    const lelen = dv.getUint16(lho + 28, true);
    const start = lho + 30 + lnlen + lelen;
    const body = data.subarray(start, start + csize);
    out.set(name, method === 0 ? body : await inflateRaw(body));
    p += 46 + nlen + elen + clen;
  }
  return out;
}

/** zip 또는 구 JSON 바이트 → 팩 객체. 두 경로의 반환 모양은 완전히 같다. */
export async function fromBytes(data: Uint8Array): Promise<Record<string, unknown>> {
  if (!isZip(data)) return JSON.parse(td.decode(data));
  const files = await unzip(data);
  const man = files.get(MANIFEST);
  if (!man) throw new Error("not a vinc pack: manifest.json missing");
  const pack: Record<string, unknown> = JSON.parse(td.decode(man));
  const frag = files.get(FRAGMENT);
  pack.fragment = frag ? JSON.parse(td.decode(frag)) : { nodes: [], edges: [] };
  const docNames = [...files.keys()]
    .filter((n) => n.startsWith(DOC_DIR) && n.endsWith(".json")).sort();
  if (docNames.length) {
    pack.documents = docNames.map((n) => JSON.parse(td.decode(files.get(n)!)));
  }
  return pack;
}

/** 사용자가 고른 파일 → 팩 객체(형식 무관). `File.text()` 를 쓰면 zip 이 깨진다. */
export async function readPackFile(f: File): Promise<Record<string, unknown>> {
  return fromBytes(new Uint8Array(await f.arrayBuffer()));
}
