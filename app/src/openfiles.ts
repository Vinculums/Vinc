// `.vincpack` 파일 연결 — OS 가 넘긴 argv 를 실제로 연다 (Q1, 2026-08-07).
//
// `tauri.conf.json` 의 `bundle.fileAssociations` 에 `.vincpack` 이 선언돼 **이미
// 출하됐는데** argv 를 읽는 코드가 어디에도 없었다. 더블클릭하면 창만 뜨고 팩은
// 안 열렸다 — 선언만 있고 동작이 없는 상태였고, 이 확장자로 공유가 이루어지고
// 있어(커뮤니티 갤러리) 사용자에겐 "깨진 연결"로 보였다.
//
// 여기서는 **새 파서를 만들지 않는다.** 러스트가 읽어 준 바이트를 기존
// `fromBytes()` 에 그대로 흘려 드롭한 파일과 **완전히 같은 경로**를 탄다
// (zip·JSON 판별 → 검증 → 임포트 시트).
//
// 두 갈래를 다 받아야 한다:
//   ① 최초 실행 — argv 가 프론트 리스너보다 **먼저** 도착한다. 러스트가 큐에 담아
//      두고 여기서 `take_pending_files` 로 가져간다(그래서 이벤트만으로는 안 된다).
//   ② 2회차 실행 — single-instance 콜백이 큐에 넣고 `vinc://open-files` 를 쏜다.
//
// 셸이 아니면(웹 Vinc·브라우저 dev) 전 경로가 no-op 다.
import { openImportSheet } from "./sheets";
import { IN_SHELL, shellInvoke } from "./api";

type Opened = { name: string; b64: string };

/** base64 → 바이트. 러스트가 손으로 짠 인코더와 짝이다(패딩 경계는 러스트 쪽 테스트). */
function bytesOf(b64: string): Uint8Array {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

async function drain(): Promise<void> {
  const invoke = await shellInvoke();
  if (!invoke) return;
  let files: Opened[] = [];
  try {
    files = (await invoke("take_pending_files")) || [];
  } catch {
    return; // 구 셸(명령 부재) — 조용히 물러난다
  }
  for (const f of files) {
    try {
      const { fromBytes } = await import("./packfile");
      openImportSheet(await fromBytes(bytesOf(f.b64)));
    } catch (e) {
      // 팩이 깨졌어도 **무음으로 끝내지 않는다** — 아무 일도 안 일어나는 것이
      // 정확히 이 버그의 증상이었다. 열지 못한 파일 이름을 남긴다.
      console.error(`[open] ${f.name} 을(를) 열지 못했다`, e);
    }
  }
}

/** 기동 시 1회(비차단). 셸이 아니면 아무것도 하지 않는다. */
export function wireFileAssociations(): void {
  if (!IN_SHELL) return;
  void drain();
  // 2회차 실행분. 리스너 등록 실패가 본업을 막지 않게 감싼다.
  void (async () => {
    try {
      const { listen } = await import("@tauri-apps/api/event");
      await listen("vinc://open-files", () => void drain());
    } catch {
      /* 이벤트 배선 실패 — 최초 실행 경로는 위에서 이미 처리됐다 */
    }
  })();
}
