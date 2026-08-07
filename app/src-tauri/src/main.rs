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
        // 파일 로그에는 **상한 없이** 전부 남긴다 — UI 버퍼는 24줄에서 끊기므로
        // 그 이후에 나온 진짜 사인(死因)이 조용히 사라지는 걸 막는다.
        log::warn!(target: "sidecar", "{line}");
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

/// 등록 가능한 로컬 MCP 호스트 — 사이드카 `vinc_mcp.install.CLIENTS` 와 짝.
/// 여기서 먼저 막는 이유는 임의 문자열이 CLI 인자로 흘러가지 않게 하기 위해서다.
const MCP_CLIENTS: [&str; 4] = ["code", "desktop", "cursor", "codex"];

/// Settings→MCP: 이 설치본을 로컬 MCP 호스트에 1회 등록한다. 번들 사이드카
/// `mcp install|uninstall --client <host>` 를 실행(서버 기동 아님, one-shot)해
/// 결과 JSON(stdout)을 프론트에 돌려준다. 실패 시 stderr/stdout 을 Err 로 전달.
async fn mcp_action(app: &tauri::AppHandle, action: &str, client: &str) -> Result<String, String> {
    if !MCP_CLIENTS.contains(&client) {
        return Err(format!("unknown client: {client}"));
    }
    let out = app
        .shell()
        .sidecar("vinc-sidecar")
        .map_err(|e| e.to_string())?
        .args(["mcp", action, "--client", client])
        .output()
        .await
        .map_err(|e| e.to_string())?;
    let stdout = String::from_utf8_lossy(&out.stdout).to_string();
    let stderr = String::from_utf8_lossy(&out.stderr).to_string();
    if out.status.success() {
        log::info!(target: "mcp", "{action} {client} ok");
        Ok(stdout)
    } else {
        let err = if stderr.trim().is_empty() { stdout } else { stderr };
        log::warn!(target: "mcp", "{action} {client} 실패: {}", err.trim());
        Err(err)
    }
}

/// Settings→MCP: 이 설치본을 로컬 MCP 호스트에 등록(install).
#[tauri::command]
async fn mcp_install(app: tauri::AppHandle, client: String) -> Result<String, String> {
    mcp_action(&app, "install", &client).await
}

/// Settings→MCP: 등록 해제(uninstall) — 호스트 설정 JSON 의 vinc 키 제거(백업 후)
/// 또는 호스트 CLI 의 `mcp remove`. install 의 역연산, 멱등.
#[tauri::command]
async fn mcp_uninstall(app: tauri::AppHandle, client: String) -> Result<String, String> {
    mcp_action(&app, "uninstall", &client).await
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

/// 세션 파일을 원자적으로 기록(temp → rename). 실패해도 앱은 계속 가지만(필수 아님)
/// **MCP 브리지가 attach 못 하는 원인**이 되므로 조용히 넘기지 않고 로그로 남긴다.
fn write_session(app: &tauri::AppHandle, port: u16, token: &str) {
    let Some(path) = session_path(app) else {
        log::warn!(target: "session", "home_dir 조회 실패 → session.json 미기록 (MCP 브리지 attach 불가)");
        return;
    };
    if let Some(dir) = path.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    let body = format!(
        "{{\"port\":{},\"token\":\"{}\",\"pid\":{},\"v\":\"0.1.9\"}}",
        port, token, std::process::id());
    let tmp = path.with_extension("json.tmp");
    let wrote = std::fs::write(&tmp, body).and_then(|_| std::fs::rename(&tmp, &path));
    match wrote {
        Ok(()) => log::info!(target: "session", "session.json 기록 {}", path.display()),
        Err(e) => log::warn!(
            target: "session",
            "session.json 기록 실패: {e} ({}) — MCP 브리지 attach 불가",
            path.display(),
        ),
    }
}

/// (Windows) 사이드카 프로세스 트리 강제 종료 — 종료 시 부트로더만 죽고 파이썬 자식이
/// 고아로 남아 `.exe` 락을 유지하는 것을 막는다(`/T` = 자식까지).
#[cfg(windows)]
fn kill_sidecar_tree(pid: u32) {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    let _ = std::process::Command::new("taskkill")
        .args(["/F", "/T", "/PID", &pid.to_string()])
        .creation_flags(CREATE_NO_WINDOW)
        .output();
}

fn main() {
    let token = session_token();
    let port = pick_free_port();
    let port_arg = port.to_string();
    let mut builder = tauri::Builder::default();
    // 단일 인스턴스 가드는 **가장 먼저** 등록(다른 플러그인 개입 전 선점). 두 번째 실행은
    // 기존 창을 포커스하고 즉시 종료 → 사이드카 중복 spawn·누수 근절.
    #[cfg(desktop)]
    {
        builder = builder.plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(win) = app.get_webview_window("main") {
                let _ = win.unminimize();
                let _ = win.set_focus();
            }
        }));
    }
    // 로깅은 single-instance **다음, 나머지 플러그인보다 먼저** — 이후 플러그인 초기화와
    // setup() 의 사이드카 spawn 이 전부 로그에 잡히게. 로그 파일 위치(identifier 기준):
    //   Windows  %APPDATA%\app.vinc.desktop\logs\vinc.log
    //   macOS    ~/Library/Logs/app.vinc.desktop/vinc.log
    //   Linux    ~/.local/share/app.vinc.desktop/logs/vinc.log
    // Webview 타깃은 쓰지 않는다 — 러스트 로그를 웹뷰 콘솔로 되쏘면 CSP·성능만 건드리고
    // 진단 가치는 파일에 이미 다 있다.
    builder = builder.plugin(
        tauri_plugin_log::Builder::new()
            .targets([
                tauri_plugin_log::Target::new(tauri_plugin_log::TargetKind::Stdout),
                tauri_plugin_log::Target::new(tauri_plugin_log::TargetKind::LogDir {
                    file_name: Some("vinc".into()),
                }),
            ])
            .level(log::LevelFilter::Info)
            // 오프라인 데스크탑이라 로그를 수거해 갈 사람이 없다 → 무한 증식 금지.
            .max_file_size(2 * 1024 * 1024)
            .rotation_strategy(tauri_plugin_log::RotationStrategy::KeepOne)
            .build(),
    );
    let app = builder
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
        .invoke_handler(tauri::generate_handler![sidecar_conn, sidecar_status, mcp_install, mcp_uninstall])
        .setup(move |app| {
            // 셸 PID 를 넘겨 heartbeat 감독을 건다(E6) — 셸이 크래시·강제종료로 죽어
            // RunEvent::Exit 가 안 불려도 사이드카가 ~1s 내 자가종료(고아 근절).
            // (Windows-안전 조회는 사이드카 server._pid_alive 참조.)
            //
            // 시작 시 이미지명 일괄 taskkill(/IM)로 "고아 회수"는 하지 않는다: 그러면
            // Settings→MCP 로 Claude 클라이언트에 등록된 브리지(`vinc-sidecar.exe mcp serve`)
            // 와 그 자식까지 같은 이미지명이라 죽여 실행 중 MCP 세션을 끊는다. 누수는
            // single-instance(중복 셸 차단)+parent-pid heartbeat(크래시 고아 자가종료)+
            // 종료 시 트리-kill 로 대신 막고, 구버전 잔존 고아는 설치 시 NSIS 훅이 정리한다.
            let parent_pid = std::process::id().to_string();
            // 동적 포트(Conn 과 동일 값) — 옛 사이드카가 고정 포트를 물고 있어도 무충돌.
            let sidecar = app
                .shell()
                .sidecar("vinc-sidecar")?
                .args([
                    "serve",
                    "--port",
                    &port_arg,
                    "--token",
                    &token,
                    "--parent-pid",
                    &parent_pid,
                ]);
            let (mut events, child) = sidecar.spawn().inspect_err(|e| {
                // spawn 자체가 실패하면 CommandEvent 조차 안 온다 → 여기서 안 남기면 증거 0.
                log::error!(target: "sidecar", "spawn failed: {e}");
            })?;
            log::info!(
                target: "sidecar",
                "spawned v{} pid={} port={} parent={}",
                app.package_info().version, child.pid(), port, parent_pid,
            );
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
            // 정상 종료의 흔적 — 다음 실행의 로그가 이 줄 없이 시작하면 직전 실행은
            // 크래시·강제종료였다는 뜻이다(고아 사이드카 조사 시 첫 단서).
            log::info!(target: "shell", "exit — 세션 파일 제거 · 사이드카 회수");
            // 세션 파일 제거(정상 종료) — 브리지가 stale 세션에 붙지 않게. 크래시로
            // 남을 수 있어 브리지는 pid 생존 + /health 로 재검증한다(파일 존재만 신뢰 X).
            if let Some(path) = session_path(app) {
                let _ = std::fs::remove_file(path);
            }
            if let Some(child) = app.state::<Sidecar>().0.lock().unwrap().take() {
                // Windows: 부트로더만 죽으면 파이썬 자식이 고아로 .exe 락을 유지 →
                // 트리째 강제 종료(child.kill 은 폴백).
                #[cfg(windows)]
                kill_sidecar_tree(child.pid());
                let _ = child.kill();
            }
        }
    });
}
