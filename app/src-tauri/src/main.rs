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
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

const SIDECAR_PORT: u16 = 8787; // 폴백 전용 — 실제 포트는 pick_free_port() 로 동적 배정.

struct Sidecar(std::sync::Mutex<Option<CommandChild>>);

/// 사이드카 수명 이벤트 기록 — 죽은 이유를 프론트 진단에 실어 보내기 위한 버퍼.
/// 이게 없으면 사이드카가 즉사해도 화면엔 "health TIMEOUT" 뿐이라 원인 추적이
/// 불가능하다(macOS 격리로 Gatekeeper 가 조용히 SIGKILL 하는 사례 — 2026-07).
struct SidecarLog(std::sync::Mutex<Vec<String>>);

impl SidecarLog {
    fn push(&self, line: String) {
        let mut v = self.0.lock().unwrap();
        if v.len() < 24 {   // 상한 — 진단 문자열이 UI 를 삼키지 않게
            v.push(line);
        }
    }
}

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

/// 사이드카가 남긴 수명 이벤트(stderr·종료코드·시그널·스폰 에러)를 프론트에 준다.
/// 프론트는 `/health` 대기 실패 시 이걸 진단 문자열에 덧붙인다 — "왜 안 떴나"가
/// 화면에 바로 보이게(실패 신호 설계: 조용한 실패 금지).
#[tauri::command]
fn sidecar_status(log: tauri::State<'_, SidecarLog>) -> String {
    let v = log.0.lock().unwrap();
    if v.is_empty() {
        "no sidecar events".into()
    } else {
        v.join(" ; ")
    }
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

/// 스폰 직전 OS 가 배정한 빈 포트를 잡아 즉시 반환 → 사이드카가 그 포트로 bind 한다.
/// 고정 8787 을 (강제종료로 잔존한) 옛 사이드카가 물고 있어도 새 실행은 다른 포트를 써
/// 충돌하지 않는다(잔존 사이드카 재발 방지). 반환~재bind 사이의 짧은 창은 루프백 임시
/// 포트라 사실상 무해하며, 실패 시 고정 포트로 폴백한다.
fn pick_free_port() -> u16 {
    std::net::TcpListener::bind("127.0.0.1:0")
        .and_then(|l| l.local_addr())
        .map(|a| a.port())
        .unwrap_or(SIDECAR_PORT)
}

/// `~/.vinc/session.json` 경로 — 사이드카 데이터 홈(파이썬 `resolve_data_dir` 의
/// `~/.vinc`)과 정합. MCP 브리지(`mcp serve`)가 이 파일을 읽어 **앱 사이드카에 attach**
/// 한다(자기 사이드카를 8787 에 따로 띄우지 않음) → 같은 `~/.vinc` 를 두 프로세스가
/// 여는 스토어 이중 개방·좀비를 근절(A1.9).
fn session_path(app: &tauri::AppHandle) -> Option<std::path::PathBuf> {
    app.path().home_dir().ok().map(|h| h.join(".vinc").join("session.json"))
}

/// 세션 파일을 원자적으로 기록(temp → rename). 실패는 무시(진단 편의일 뿐 필수 아님).
fn write_session(app: &tauri::AppHandle, port: u16, token: &str) {
    let Some(path) = session_path(app) else { return };
    if let Some(dir) = path.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    let body = format!(
        "{{\"port\":{},\"token\":\"{}\",\"pid\":{},\"v\":\"0.1.9\"}}",
        port, token, std::process::id());
    let tmp = path.with_extension("json.tmp");
    if std::fs::write(&tmp, body).is_ok() {
        let _ = std::fs::rename(&tmp, &path);
    }
}

fn main() {
    let token = session_token();
    let port = pick_free_port();
    let port_arg = port.to_string();
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        .manage(Sidecar(std::sync::Mutex::new(None)))
        .manage(SidecarLog(std::sync::Mutex::new(Vec::new())))
        .manage(Conn {
            port,
            token: token.clone(),
        })
        .invoke_handler(tauri::generate_handler![sidecar_conn, sidecar_status, mcp_install])
        .setup(move |app| {
            // 동적 포트(Conn 과 동일 값) — 옛 사이드카가 고정 포트를 물고 있어도 무충돌.
            let sidecar = app
                .shell()
                .sidecar("vinc-sidecar")?
                .args(["serve", "--port", &port_arg, "--token", &token]);
            let (mut events, child) = sidecar.spawn()?;
            *app.state::<Sidecar>().0.lock().unwrap() = Some(child);
            // MCP 브리지가 이 사이드카를 찾아 붙도록 세션 파일 기록(A1.9).
            write_session(&app.handle(), port, &token);
            // 사이드카 수명 이벤트를 흘려보내지 않고 기록한다 — 프론트가
            // `sidecar_status` 로 읽어 진단에 싣는다(조용한 즉사 방지).
            let handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                while let Some(ev) = events.recv().await {
                    let line = match ev {
                        CommandEvent::Stderr(b) => {
                            let s = String::from_utf8_lossy(&b).trim().to_string();
                            if s.is_empty() { continue; }
                            format!("stderr: {}", s.chars().take(200).collect::<String>())
                        }
                        CommandEvent::Error(e) => format!("spawn error: {e}"),
                        CommandEvent::Terminated(p) => format!(
                            "terminated: code={:?} signal={:?}", p.code, p.signal),
                        _ => continue,
                    };
                    handle.state::<SidecarLog>().push(line);
                }
            });
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
            // 세션 파일 제거(정상 종료) — 브리지가 stale 세션에 붙지 않게. 크래시로
            // 남을 수 있어 브리지는 pid 생존 + /health 로 재검증한다(파일 존재만 신뢰 X).
            if let Some(path) = session_path(app) {
                let _ = std::fs::remove_file(path);
            }
            if let Some(child) = app.state::<Sidecar>().0.lock().unwrap().take() {
                let _ = child.kill();
            }
        }
    });
}
