// 데이터 호출 하나가 실패한 모양(`ApiError`)과 오류 봉투를 읽는 곳 하나. 계정은 `{error:{code,message,reason}}`,
// 사이드카와 통과로는 `{error:"..."}` 를 준다. 화면은 이 필드로 가르고, 영어 서버 문자열이나 문구 속 JSON 을 다시 뜯지 않는다.
// 어떤 실패에 어떤 필드가 실리는지는 전송 경로(api.ts 의 accountCall · localCall)가 정하고, 그 값은 여기의 두 함수가 만든다.

/** 계정 모드의 실패 종류. `signIn` = 키가 더는 통하지 않는다 · `teamKey` = 팀 키가 필요하다 ·
 *  `unreachable` = 계정 · 허브에 못 닿았다 · `other` = 그 밖. */
export type AccountFailureKind = "signIn" | "teamKey" | "unreachable" | "other";

export class ApiError extends Error {
  /** HTTP 상태. 응답이 없었으면 0. */
  status: number;
  /** 오류 봉투의 코드. 봉투가 없거나 본문이 JSON 이 아니면 undefined. */
  code: string | undefined;
  /** 계정 모드 실패의 종류. 계정 경로의 실패에만 있다. */
  account?: AccountFailureKind;
  /** 스토어를 못 여는 503(사이드카는 살아 있다). 화면이 "데이터가 없다"와 갈라 말해야 하는 실패다. */
  storeDown?: boolean;
  /** 스토어를 못 연 사유(사이드카의 `open_error`). `storeDown` 일 때만. */
  reason?: string;
  /** 실패한 응답의 JSON 본문. 실패 문구가 본문을 그대로 싣는 실패에만 있다: 사이드카 · 웹 경로의 일반 실패,
   *  그리고 계정 경로에서 봉투에 코드도 문구도 없던 실패. */
  body?: unknown;

  constructor(message: string, init: { status: number; code?: string; account?: AccountFailureKind;
                                       storeDown?: boolean; reason?: string; body?: unknown }) {
    super(message);
    this.status = init.status;
    this.code = init.code;
    if (init.account !== undefined) this.account = init.account;
    if (init.storeDown !== undefined) this.storeDown = init.storeDown;
    if (init.reason !== undefined) this.reason = init.reason;
    if (init.body !== undefined) this.body = init.body;
  }
}

/** 오류 봉투에서 코드 · 문구 · 사유를 읽는다. 봉투가 아니면 셋 다 빈 문자열이다.
 *  `detail` 은 계정의 문구(없으면 코드), `reason` 은 계정이 거절의 까닭을 싣는 자리(`extra.reason` 이 먼저)다. */
export function parseEnvelope(b: unknown): { code: string; detail: string; reason: string } {
  const e = (b as { error?: unknown } | null | undefined)?.error as
    { code?: unknown; message?: unknown; reason?: unknown; extra?: { reason?: unknown } } | string | null | undefined;
  const code = typeof e === "object" ? String(e?.code || "") : String(e || "");
  const detail = typeof e === "object" ? String(e?.message || code) : code;
  const reason = typeof e === "object" ? String(e?.extra?.reason || e?.reason || "") : "";
  return { code, detail, reason };
}

/** 사이드카 · 웹 경로의 실패 응답 하나(`status` 와 본문 글자 그대로). 웹의 401(로그인으로 보내기)은 부르는 쪽이 먼저 가른다.
 *  503 은 스토어를 못 연 것이라 사유를 따로 싣고, 나머지는 `"<상태> <본문>"` 문구에 봉투의 코드를 붙인다. */
export function localFailure(status: number, text: string): ApiError {
  let parsed: unknown;
  let json = false;
  try { parsed = JSON.parse(text); json = true; } catch { /* JSON 오류 봉투가 아니면 상태만 남긴다. */ }
  const raw = (b: unknown) => {
    const e = (b as { error?: unknown } | null | undefined)?.error as { code?: unknown } | string | null | undefined;
    return (typeof e === "object" ? e?.code : e) as string | undefined;
  };
  if (status === 503) {
    const b = (json ? parsed : {}) as { open_error?: unknown; error?: unknown } | null;
    return new ApiError(String(b?.open_error || b?.error || "no reachable store"),
      { status, storeDown: true, reason: String(b?.open_error || ""), code: raw(b) });
  }
  return new ApiError(`${status} ${text}`, json ? { status, code: raw(parsed), body: parsed } : { status });
}
