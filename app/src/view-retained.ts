import { viewSessionCache, type Entry, type RetainedSnapshot } from "./view-session-cache";

/** 스페이스를 옮길 때만 쓰는 짧은 보관소.
 *
 * 스페이스 전환은 페이지 전체를 다시 불러온다(`settings.ts`). 세션 캐시는 메모리에만 있고
 * 페이지를 떠날 때 비워지므로, 방금 떠난 스페이스로 돌아오면 그래프와 가지 배치를 처음부터
 * 다시 읽고 계산했다. 이 보관소는 **떠나는 순간에만** 그 스페이스의 완료 항목을 IndexedDB 에
 * 쓰고, 돌아왔을 때 새로 확인한 신원·변경 표식과 맞을 때만 되돌린다.
 *
 * 경계(바꾸면 안 되는 것):
 *  - 쓰는 때는 스페이스 전환 직전 한 번뿐이다. 평소의 읽기는 아무것도 디스크에 남기지 않는다.
 *  - 키는 세션 문자열의 SHA-256 이다. 세션 문자열은 토큰·CSRF 값을 담으므로 그대로 두지 않는다.
 *  - 되돌릴 때 세션과 변경 표식이 둘 다 맞아야 한다. 하나라도 다르면 버린다. 쓰이면 지운다.
 *  - 수명은 `TTL_MS`, 기록 수와 바이트에 상한이 있다. 만료된 것은 열 때마다 걷어 낸다.
 *  - 로그인이 풀리거나 계정·모드가 바뀌면(`viewSessionCache.purge`) 전부 지운다.
 * 근거: [[decision:retain-the-left-space-view-for-a-short-handoff-2026-10-12]] */
export const TTL_MS = 15 * 60_000;
export const MAX_RECORDS = 4;
export const MAX_BYTES = 32 * 1024 * 1024;

export type RetainedRecord = { id: string; who: string; revision: string; expires: number;
  bytes: number; entries: [string, Entry][] };

export interface RetainedBackend {
  get(id: string): Promise<RetainedRecord | undefined>;
  put(record: RetainedRecord): Promise<void>;
  delete(id: string): Promise<void>;
  all(): Promise<RetainedRecord[]>;
  clear(): Promise<void>;
  /** 이 보관소가 이미 만들어져 있는가. 없으면 비우려고 새로 만들지 않는다. */
  exists(): Promise<boolean>;
}

const DB = "vinc-view-retained", STORE = "records";

/** IndexedDB 백엔드. 없으면(비공개 창·차단) null 이고 보관 없이 종전대로 동작한다. */
export function idbBackend(factory: IDBFactory | undefined = typeof indexedDB === "undefined" ? undefined : indexedDB): RetainedBackend | null {
  if (!factory) return null;
  let opening: Promise<IDBDatabase> | undefined;
  const open = () => opening ||= new Promise<IDBDatabase>((resolve, reject) => {
    const req = factory.open(DB, 1);
    req.onupgradeneeded = () => { req.result.createObjectStore(STORE, { keyPath: "id" }); };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => { opening = undefined; reject(req.error); };
    req.onblocked = () => { opening = undefined; reject(new Error("blocked")); };
  });
  const run = async <T>(mode: IDBTransactionMode, f: (s: IDBObjectStore) => IDBRequest<T>): Promise<T> => {
    const db = await open();
    return new Promise<T>((resolve, reject) => {
      const tx = db.transaction(STORE, mode), req = f(tx.objectStore(STORE));
      tx.oncomplete = () => resolve(req.result);
      tx.onerror = () => reject(tx.error); tx.onabort = () => reject(tx.error);
    });
  };
  return {
    get: id => run("readonly", s => s.get(id) as IDBRequest<RetainedRecord | undefined>),
    put: async record => { await run("readwrite", s => s.put(record)); },
    delete: async id => { await run("readwrite", s => s.delete(id)); },
    all: () => run("readonly", s => s.getAll() as IDBRequest<RetainedRecord[]>),
    clear: async () => { await run("readwrite", s => s.clear()); },
    exists: async () => {
      const list = (factory as IDBFactory & { databases?: () => Promise<{ name?: string }[]> }).databases;
      if (typeof list !== "function") return true;
      try { return (await list.call(factory)).some(d => d.name === DB); } catch { return true; }
    },
  };
}

/** 세션 문자열의 지문. 토큰이 디스크에 남지 않게 한다. */
export async function sessionId(session: string): Promise<string> {
  const bytes = new TextEncoder().encode(session);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map(b => b.toString(16).padStart(2, "0")).join("");
}

/** "보관본이 있을 수 있다"는 시각 하나. 데이터가 아니라 만료 시각이라 localStorage 에 둬도
 * 되고, 화면을 그리기 전에 기록 전체를 읽지 않고도 기다릴지 정하게 해 준다. */
export interface RetainedMark {
  get(): number; set(until: number): void; clear(): void;
  /** 마지막으로 보관본을 버린 시각. 다른 탭의 저장이 그 뒤에 끝나면 그 기록을 되지운다. */
  purgedAt(): number; markPurged(at: number): void;
}
const MARK_KEY = "vinc.view.retained.until", PURGED_KEY = "vinc.view.retained.purged";
export const storageMark: RetainedMark = {
  get() { try { return Number(localStorage.getItem(MARK_KEY)) || 0; } catch { return 0; } },
  set(until) { try { localStorage.setItem(MARK_KEY, String(until)); } catch { /* 없어도 동작한다 */ } },
  clear() { try { localStorage.removeItem(MARK_KEY); } catch { /* 없어도 동작한다 */ } },
  purgedAt() { try { return Number(localStorage.getItem(PURGED_KEY)) || 0; } catch { return 0; } },
  markPurged(at) { try { localStorage.setItem(PURGED_KEY, String(at)); } catch { /* 없어도 동작한다 */ } },
};

/** 세션 문자열(`[스코프, 신원]`)의 **로그인** 지문. 스코프 키 안의 CSRF 값은 로그인마다 새로
 * 뽑히는 비밀이라, 다른 사람이 같은 브라우저로 들어오거나 같은 사람이 다시 로그인하면 값이
 * 달라져 이전 기록이 쓸모없어진다. 이메일 같은 추측 가능한 값으로 지문을 만들지 않는다
 * (프로필을 읽을 수 있는 사람이 추측을 오프라인으로 확인할 수 있다). CSRF 값이 없으면 신원
 * 문자열로 물러난다(웹이 아닌 환경과 테스트). 읽을 수 없으면 빈 문자열이다. */
export async function identityId(session: string, hash: (s: string) => Promise<string> = sessionId): Promise<string> {
  try {
    const [scope, identity] = JSON.parse(session) as [unknown, unknown];
    let secret: unknown;
    try { secret = JSON.parse(String(scope))?.[6]; } catch { secret = undefined; }
    const basis = typeof secret === "string" && secret ? secret : identity;
    return typeof basis === "string" && basis ? await hash("login:" + basis) : "";
  } catch { return ""; }
}

export class RetainedViews {
  constructor(private backend: RetainedBackend | null, private clock = () => Date.now(),
    private ttl = TTL_MS, private maxRecords = MAX_RECORDS, private maxBytes = MAX_BYTES,
    private hash: (session: string) => Promise<string> = sessionId,
    private mark: RetainedMark = storageMark) {}

  private async live(): Promise<RetainedRecord[]> {
    if (!this.backend) return [];
    const now = this.clock(), rows = await this.backend.all();
    const stale = rows.filter(r => r.expires <= now);
    await Promise.all(stale.map(r => this.backend!.delete(r.id)));
    return rows.filter(r => r.expires > now);
  }

  /** 보관된 항목이 있을 수 있는가. 화면을 그리기 전에 신원 확인을 기다릴지 정하는 데 쓴다.
   *  기록을 읽지 않는다: 만료 시각 하나만 본다. */
  has(): boolean {
    return !!this.backend && this.mark.get() > this.clock();
  }

  async save(snapshot: RetainedSnapshot): Promise<boolean> {
    if (!this.backend || !snapshot.entries.length) return false;
    try {
      const id = await this.hash(snapshot.session);
      const who = await identityId(snapshot.session, this.hash);
      if (!who) return false;
      const bytes = snapshot.entries.reduce((n, [, e]) => n + e.size, 0);
      if (bytes > this.maxBytes) return false;
      const started = this.clock();
      const expires = started + this.ttl;
      const others = (await this.live()).filter(r => r.id !== id).sort((a, b) => b.expires - a.expires);
      let kept = bytes;
      const keep: RetainedRecord[] = [];
      for (const r of others) {
        if (keep.length + 1 >= this.maxRecords || kept + r.bytes > this.maxBytes) {
          await this.backend.delete(r.id); continue;
        }
        keep.push(r); kept += r.bytes;
      }
      // 표식을 먼저 둔다: 기록만 쓰이고 표식이 없으면 아무도 그 기록을 찾지 못한다.
      this.mark.set(Math.max(expires, ...keep.map(r => r.expires)));
      await this.backend.put({ id, who, revision: snapshot.revision, expires, bytes, entries: snapshot.entries });
      // 저장 도중 다른 탭이 보관본을 버렸다면 방금 쓴 기록도 버린다.
      if (this.mark.purgedAt() >= started) { await this.backend.delete(id); return false; }
      return true;
    } catch { return false; }
  }

  /** 이 세션의 항목을 꺼낸다. 표식이 맞으면 항목을, 아니면 null. 어느 쪽이든 기록은 지운다. */
  async take(session: string, revision: string): Promise<{ entries: [string, Entry][]; expires: number } | null> {
    if (!this.backend || !session || !revision) return null;
    try {
      const id = await this.hash(session);
      const who = await identityId(session, this.hash);
      // 이 신원의 것이 아닌 기록은 쓸 일이 없다. 같은 브라우저를 다른 사람이 쓰면 지운다.
      for (const other of await this.live()) {
        if (other.who !== who) await this.backend.delete(other.id);
      }
      const record = await this.backend.get(id);
      if (!record) return null;
      await this.backend.delete(id);
      if (record.expires <= this.clock() || record.revision !== revision) return null;
      return { entries: record.entries, expires: record.expires };
    } catch { return null; }
  }

  async clear(): Promise<void> {
    this.mark.markPurged(this.clock());
    this.mark.clear();
    try { await this.backend?.clear(); } catch { /* 보관은 최선 노력이다 */ }
  }

  /** 앱이 열릴 때마다 부른다. 보관 시한이 지났거나 표식이 없으면 남은 기록은 아무도 쓰지 않는
   *  것이므로(탭을 닫았거나 표식이 어긋난 경우) 통째로 비운다. 기록을 읽지 않는다. */
  async sweep(): Promise<void> {
    if (!this.backend || this.mark.get() > this.clock()) return;
    try {
      if (!(await this.backend.exists())) return;
      await this.backend.clear();
    } catch { /* 보관은 최선 노력이다 */ }
    this.mark.clear();
  }
}

export const retainedViews = new RetainedViews(idbBackend());
viewSessionCache.onPurge(() => { void retainedViews.clear(); });
void retainedViews.sweep();

/** 스페이스를 떠나기 직전에 부른다. 오래 걸리면 기다리지 않고 넘어간다. */
export async function retainBeforeSwitch(limitMs = 2000): Promise<boolean> {
  const snapshot = viewSessionCache.snapshot();
  if (!snapshot) return false;
  const done = retainedViews.save(snapshot);
  const timer = new Promise<boolean>(resolve => setTimeout(() => resolve(false), limitMs));
  return Promise.race([done, timer]);
}

/** 화면을 그리기 전에 부른다. 신원과 표식이 새로 확인된 뒤여야 한다. 되돌린 개수. */
export async function restoreRetained(session: string, revision: string): Promise<number> {
  const taken = await retainedViews.take(session, revision);
  if (!taken) return 0;
  return viewSessionCache.restore(session, revision, taken.entries, taken.expires);
}
