// Vinc Tauri shell (P4 — D43) — control plane only.
// Spawns the bundled sidecar (externalBin `vinc-sidecar`) with a per-session
// random token, and hands the connection info (port + token) to the webview via
// the `sidecar_conn` command — the frontend invokes it on startup and configures
// its HTTP client. (A `win.eval` localStorage write is kept as a best-effort
// fallback; it is timing-fragile, which is why the command is the source of truth.)
// All data flows over localhost HTTP (never IPC) — see Vinc-docs vinc/ARCHITECTURE.md.
// Data home defaults to ~/.vinc (D43).
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use tauri::{Manager, RunEvent};
use tauri_plugin_shell::process::CommandChild;
use tauri_plugin_shell::ShellExt;

const SIDECAR_PORT: u16 = 8787;

struct Sidecar(std::sync::Mutex<Option<CommandChild>>);

/// 프론트가 기동 시 `invoke("sidecar_conn")` 로 받는 사이드카 접속정보.
#[derive(Clone, serde::Serialize)]
struct Conn {
    port: u16,
    token: String,
}

/// 사이드카 접속정보(포트·세션 토큰)를 프론트에 확정적으로 전달한다.
/// `win.eval` 은 페이지 로드 타이밍에 불안정 → 이 커맨드가 정본 경로다.
#[tauri::command]
fn sidecar_conn(conn: tauri::State<'_, Conn>) -> Conn {
    conn.inner().clone()
}

/// Settings→MCP: 이 설치본을 로컬 Claude 클라이언트에 1회 등록한다. 번들 사이드카
/// `mcp install --client <desktop|code>` 를 실행(서버 기동 아님, one-shot)해 결과
/// JSON(stdout)을 프론트에 돌려준다. 실패 시 stderr/stdout 을 Err 로 전달.
#[tauri::command]
async fn mcp_install(app: tauri::AppHandle, client: String) -> Result<String, String> {
    if client != "desktop" && client != "code" {
        return Err(format!("unknown client: {client}"));
    }
    let out = app
        .shell()
        .sidecar("vinc-sidecar")
        .map_err(|e| e.to_string())?
        .args(["mcp", "install", "--client", &client])
        .output()
        .await
        .map_err(|e| e.to_string())?;
    let stdout = String::from_utf8_lossy(&out.stdout).to_string();
    let stderr = String::from_utf8_lossy(&out.stderr).to_string();
    if out.status.success() {
        Ok(stdout)
    } else {
        Err(if stderr.trim().is_empty() { stdout } else { stderr })
    }
}

/// 로컬 세션 토큰 — 실행마다 새로 생성(F4: 자격은 세션 한정).
/// RandomState 는 OS 엔트로피로 시드되므로 외부 crate 없이 충분하다.
fn session_token() -> String {
    use std::collections::hash_map::RandomState;
    use std::hash::{BuildHasher, Hasher};
    (0..4u64)
        .map(|i| {
            let mut h = RandomState::new().build_hasher();
            h.write_u64(i);
            format!("{:016x}", h.finish())
        })
        .collect()
}

fn main() {
    let token = session_token();
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .manage(Sidecar(std::sync::Mutex::new(None)))
        .manage(Conn {
            port: SIDECAR_PORT,
            token: token.clone(),
        })
        .invoke_handler(tauri::generate_handler![sidecar_conn, mcp_install])
        .setup(move |app| {
            // 포트는 SIDECAR_PORT 와 동일해야 함(Conn 과 동기) — v0.1.1 에서 CI 빌드에
            // 성공한 리터럴 args 패턴 유지.
            let sidecar = app
                .shell()
                .sidecar("vinc-sidecar")?
                .args(["serve", "--port", "8787", "--token", &token]);
            let (_events, child) = sidecar.spawn()?;
            *app.state::<Sidecar>().0.lock().unwrap() = Some(child);
            // 폴백(정본은 sidecar_conn 커맨드): 웹뷰 localStorage 에도 토큰 주입 시도.
            if let Some(win) = app.get_webview_window("main") {
                let _ = win.eval(&format!(
                    "localStorage.setItem('vinc.sidecar.token','{token}')"
                ));
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Vinc");
    app.run(|app, event| {
        if let RunEvent::Exit = event {
            if let Some(child) = app.state::<Sidecar>().0.lock().unwrap().take() {
                let _ = child.kill();
            }
        }
    });
}
