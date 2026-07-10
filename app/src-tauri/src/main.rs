// Vinc Tauri shell (P4 scaffold) — control plane only.
// Spawns the Python sidecar (api.cli serve), passes the session token to the
// webview, and owns window lifecycle + native file dialogs. All data flows
// over localhost HTTP (never IPC) — see Vinc-docs vinc/ARCHITECTURE.md.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .run(tauri::generate_context!())
        .expect("error while running Vinc");
}
