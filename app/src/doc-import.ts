import { COPY } from "./copy";
import { api, remote } from "./api";

/** 문서 정본 import(D45) — 팩이 아니라 **원문 파일**을 그대로 그래프에 넣는 경로.
 *
 *  파이프는 원래 뚫려 있었다: `/doc/put`·`vinc_doc_put`·앱 내 편집기가 전부
 *  같은 `put_doc` 로 수렴하는데 드롭존만 팩·프래그먼트를 받았다. 그래서 .md 를
 *  끌어다 놓으면 팩 파서의 `JSON.parse` 가 먼저 죽고 SyntaxError 원문이 그대로
 *  사용자에게 노출됐다 — 있는 능력을 UI 가 못 보여주던 상태.
 *
 *  진입점이 둘(Intake 드롭존 · 상단 바 `가져오기`)이고 accept 목록도 두 곳에
 *  나뉘어 있어, 판별·적재 규칙은 여기 한 곳에 둔다.
 *
 *  경로가 둘인 이유:
 *    · 텍스트 → `/doc/put`(문자열 그대로). base64 팽창이 없어 웹 상한을 온전히 쓴다.
 *    · 바이너리(PDF)·**깨진 디코드** → `/doc/put-file`(바이트를 base64 로).
 *      브라우저 `File.text()` 는 UTF-8 로만 읽어 cp949 저장본을 U+FFFD 로 뭉갠다.
 *      그 흔적이 보이면 원본 바이트를 넘겨 서버 디코드 사다리에 맡긴다 — 데스크톱
 *      CLI 와 같은 결과가 나오도록. */

/** 원문 텍스트로 받는 확장자(서버 `textextract.TEXT_EXTS` 와 같은 집합). */
const TEXT_EXT = /\.(md|markdown|txt|text|rst)$/i;
/** 바이트로 넘겨 서버가 추출하는 확장자. */
const BIN_EXT = /\.pdf$/i;
/** 드롭존·picker 가 받는 전체 집합. 제목을 뽑을 때도 이 앵커를 쓴다. */
const DOC_EXT = /\.(md|markdown|txt|text|rst|pdf)$/i;

export const isDocFile = (f: File): boolean => DOC_EXT.test(f.name);

/** 웹 본문 상한 — 허브 `MAX_BODY`(4MB · vinc_mcp/http_server.py)보다 낮게 잡는다.
 *  허브는 초과 본문을 413 이 아니라 **빈 본문**으로 돌려서 사이드카가 그걸
 *  "missing field: text" 400 으로 받는다. 사용자에겐 크기 문제가 형식 문제로
 *  둔갑해 보이므로, 넘기기 전에 여기서 막고 읽히는 문구를 준다.
 *  이 상한은 **계정을 지나는 모든 길**에 걸린다: 웹과 데스크탑 계정 모드(PR 401 이후 기본).
 *  상한이 없는 것은 로컬 모드(사이드카 직결)뿐이다. 그래서 문구는 "브라우저" 가 아니라
 *  "계정" 을 말한다(데스크탑에서 "데스크탑 앱은 상한이 없다" 고 말하던 것을 고쳤다). */
const WEB_BODY_LIMIT = 3_800_000;
/** base64 는 4/3 로 부푼다 — 바이트 경로의 원본 상한은 그만큼 낮다. */
const WEB_BINARY_LIMIT = Math.floor((WEB_BODY_LIMIT * 3) / 4);

type DocImport = {
  docId: string;
  title: string;
  /** F5 — 내용이 그대로면 서버가 재청킹을 건너뛴다. 실패가 아니라 성공으로 센다. */
  skipped: boolean;
  /** VIN-362: 서버의 적재 결과로 신규·갱신·무변경을 구분한다. 구 서버는 보관만 말한다. */
  outcome: "added" | "refreshed" | "unchanged" | "saved";
};

type Coded = Error & { code?: string };

function coded(code: string, message: string): Coded {
  const e = new Error(message) as Coded;
  e.code = code;
  return e;
}

function tooLarge(name: string, limitBytes: number): Coded {
  const mb = (limitBytes / (1024 * 1024)).toFixed(1).replace(/\.0$/, "");
  return coded("doc_too_large", COPY.ui.intakeUi.tooLarge(name, mb));
}

/** 서버가 강등(executed=false)으로 돌려준 사유 코드를 읽히는 문구로 바꾼다.
 *  코드를 모르면 원문 메시지를 그대로 들고 간다(조용히 삼키지 않는다). */
function serverFailure(res: { error?: string }, name: string): Coded {
  const code = res.error || "";
  // 덱은 키가 고정된 객체다(그래야 Deck 타입이 EN↔KO 누락을 잡는다) — 동적 코드
  // 조회만 여기서 좁힌다. 모르는 코드는 원문 메시지를 들고 일반 경로로 간다.
  const table: Partial<Record<string, (n: string) => string>> =
    COPY.ui.intakeUi.docError;
  const make = table[code];
  return make ? coded("doc_rejected", make(name)) : new Error(code || "doc put failed");
}

/** 크기 초과·형식 거부처럼 **원인이 확정된** 실패만 전용 문구를 돌려준다.
 *  그 밖은 null — 호출부의 기존 오류 경로(진단 줄 포함)를 그대로 쓴다. */
export function docImportError(e: unknown, _name: string): string | null {
  const err = e as Coded | null;
  return err && (err.code === "doc_too_large" || err.code === "doc_rejected")
    ? err.message : null;
}

/** origin(파일명) → doc_id 색인. 같은 파일을 고쳐서 다시 넣으면 **새 문서가
 *  아니라 갱신**이 되게 한다. `make_doc_id` 가 content_hash 를 포함하므로 id 를
 *  주지 않으면 편집본은 언제나 새 문서가 된다 — 갱신 의미론은 클라가 id 를 찾아
 *  줘야만 성립한다. 조회가 실패하면 빈 색인(전부 신규)으로 계속한다: 목록 하나
 *  때문에 적재 자체를 막지는 않는다. */
export async function originIndex(owner: string): Promise<Map<string, string>> {
  const idx = new Map<string, string>();
  try {
    const res = await api.docList(owner);
    for (const d of res.docs || []) if (d.origin) idx.set(d.origin, d.doc_id);
  } catch { /* 색인 없이 진행 */ }
  return idx;
}

/** 큰 배열에 spread 를 쓰면 스택이 터진다(수 MB PDF) — 청크로 잘라 넘긴다. */
function toBase64(bytes: Uint8Array): string {
  let bin = "";
  const step = 0x8000;
  for (let i = 0; i < bytes.length; i += step) {
    bin += String.fromCharCode(...bytes.subarray(i, i + step));
  }
  return btoa(bin);
}

function guardJsonSize(body: unknown, name: string): void {
  // 계정 프록시의 본문 상한은 웹과 셸 계정 모드에 똑같이 걸린다(둘 다 계정을 지난다).
  if (!remote()) return;
  const n = new TextEncoder().encode(JSON.stringify(body)).length;
  if (n > WEB_BODY_LIMIT) throw tooLarge(name, WEB_BODY_LIMIT);
}

/** 파일 1개 → 정본 문서 1개. origin 에 파일명을 실어 재드롭 식별의 기반을 만든다
 *  (표시는 하지 않는다 — origin 은 남의 기기 경로일 수 있다는 기존 규칙 유지). */
export async function importDocFile(
  f: File, owner: string, index?: Map<string, string>,
): Promise<DocImport> {
  const title = f.name.replace(DOC_EXT, "");
  const origin = f.name;
  const id = index?.get(origin);
  const binary = BIN_EXT.test(f.name) || !TEXT_EXT.test(f.name);

  if (remote() && f.size > (binary ? WEB_BINARY_LIMIT : WEB_BODY_LIMIT)) {
    throw tooLarge(f.name, binary ? WEB_BINARY_LIMIT : WEB_BODY_LIMIT);
  }

  let res: { executed: boolean; doc_id?: string; skipped?: boolean; created?: boolean; error?: string };
  let text: string | null = null;
  if (!binary) {
    text = await f.text();
    // U+FFFD 가 보이면 브라우저 UTF-8 디코드가 원문을 망친 것이다(cp949 등) —
    // 그 상태로 저장하면 정본에 손상이 박힌다. 바이트 경로로 되돌린다.
    if (text.includes("�")) text = null;
  }

  if (text !== null) {
    const body = { owner, text, id, title, origin };
    guardJsonSize(body, f.name);
    // ⚠ 인자 순서 주의: docPut(owner, text, id?, title?, contentTime?, origin?).
    // D72 가 contentTime 을 5번째로 끼워 넣을 때 이 호출부만 갱신이 누락돼 origin 이
    // content_time 자리로 들어갔다(2026-08-02 발견). 증상이 조용했다 — origin 이 안
    // 실려 재드롭 식별(origin→doc_id 색인)이 매번 빗나가 같은 파일이 새 문서가 되고,
    // 동시에 content_time 에 파일명이 박혀 타임라인의 내용 시점이 오염됐다.
    // 파일 임포트는 내용 시점을 모르므로 contentTime 은 undefined 로 둔다(서버 보존).
    res = await api.docPut(owner, text, id, title, undefined, origin);
  } else {
    const b64 = toBase64(new Uint8Array(await f.arrayBuffer()));
    guardJsonSize({ owner, filename: f.name, data_b64: b64, id, title }, f.name);
    res = await api.docPutFile(owner, f.name, b64, id, title);
  }

  if (!res.executed) throw serverFailure(res, f.name);
  const docId = res.doc_id || id || "";
  if (docId) index?.set(origin, docId);
  const outcome = res.skipped === true ? "unchanged"
    : res.created === true ? "added" : res.created === false ? "refreshed" : "saved";
  return { docId, title, skipped: res.skipped === true, outcome };
}

/** 적재 후 결정론 추출(마크다운 헤딩 → 개념 후보)을 **미리보기**로만 돌린다.
 *  적용은 사용자가 한 번 눌러야 한다: 임의 문서의 헤딩이 그대로 개념이 되면
 *  그래프가 오염되고, 들어간 개념은 `status='proposed'` 라 정리도 사람 일이다.
 *  한 건이 실패해도 나머지는 계속한다. */
export async function previewExtract(owner: string, docIds: string[]): Promise<number> {
  let n = 0;
  for (const id of docIds) {
    if (!id) continue;
    try {
      const res = await api.docExtract(owner, id, false);
      if (res.executed) n += (res.candidates || []).length;
    } catch { /* 부분 실패 허용 */ }
  }
  return n;
}

/** `refused` = 서버가 반영을 거절한 문서 수(대개 주제가 없어서). 여태 `executed` 를 안 봐서
 *  거절이 "개념 +0" 으로만 보였다 — 부르는 쪽이 그 수로 이유를 말한다. */
export async function applyExtract(
  owner: string, docIds: string[],
): Promise<{ created: number; mentions: number; refused: number }> {
  let created = 0, mentions = 0, refused = 0;
  for (const id of docIds) {
    if (!id) continue;
    try {
      const res = await api.docExtract(owner, id, true);
      if (!res.executed) { refused++; continue; }
      created += res.applied?.created || 0;
      mentions += res.mentions || 0;
    } catch { refused++; }
  }
  return { created, mentions, refused };
}
