// Vinc Tauri shell (P4 — D43) — control plane only.
// Spawns the bundled sidecar (externalBin `vinc-sidecar`) with a per-session
// random token, hands the token to the webview, and owns window lifecycle +
// native file dialogs. All data flows over localhost HTTP (never IPC) — see
// Vinc-docs vinc/ARCHITECTURE.md. Data home defaults to ~/.vinc (D43).
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use tauri::{Manager, RunEvent};
use tauri_plugin_shell::process::CommandChild;
use tauri_plugin_shell::ShellExt;

struct Sidecar(std::sync::Mutex<Option<CommandChild>>);

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
        .setup(move |app| {
            let sidecar = app
                .shell()
                .sidecar("vinc-sidecar")?
                .args(["serve", "--port", "8787", "--token", &token]);
            let (_events, child) = sidecar.spawn()?;
            *app.state::<Sidecar>().0.lock().unwrap() = Some(child);
            if let Some(win) = app.get_webview_window("main") {
                win.eval(&format!(
                    "localStorage.setItem('vinc.sidecar.token','{token}')"
                ))?;
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
