import { api, cfg, shellInvoke, type Scope } from "./api";

export type DeleteOutcome = "deleted" | "present" | "unknown";

// 문서·키·서버 메시지는 로그에 싣지 않는다. 상태와 오류 코드만 남긴다.
async function logFailure(operation: string, failure: any): Promise<void> {
  const status = Number(failure?.status) || 0;
  const rawCode = failure?.code || failure?.error?.code || failure?.error;
  const code = typeof rawCode === "string" && /^[a-z_][a-z0-9_]{0,63}$/i.test(rawCode)
    ? rawCode : status ? "http_error" : "network_or_operation_error";
  const message = `${operation} failed status=${status} code=${code}`;
  console.warn(`[doc-delete] ${message}`);
  // 셸에 이미 등록된 log:default 플러그인(Warn=4)으로 vinc.log 에도 남긴다.
  const invoke = await shellInvoke();
  if (invoke) await invoke("plugin:log|log", { level: 4, message, location: "doc-delete" })
    .catch(() => undefined);
}

/** 삭제가 끝났다는 증거는 정확한 문서 읽기의 부재 응답뿐이다. */
export async function checkDocumentDeletion(owner: string, id: string,
                                          scope: Scope): Promise<DeleteOutcome> {
  try {
    const doc = await api.docGet(owner, id, scope);
    if (doc.executed) return "present";
    if (doc.error === "unknown doc_id") return "deleted";
    await logFailure("doc/read after delete", doc);
  } catch (failure: any) {
    if (failure?.status === 404 && failure?.code === "not_found") return "deleted";
    await logFailure("doc/read after delete", failure);
  }
  return "unknown";
}

/** 응답을 잃었다고 삭제를 다시 보내지 않는다. 같은 스페이스에서 결과를 읽는다. */
export async function deleteDocument(owner: string, id: string,
    scope: Scope = { team: cfg.team || null }): Promise<DeleteOutcome> {
  try {
    const result = await api.docDelete(owner, id, scope);
    if (result.executed) return "deleted";
    await logFailure("doc/delete", result);
  } catch (failure) {
    await logFailure("doc/delete", failure);
  }
  return checkDocumentDeletion(owner, id, scope);
}
