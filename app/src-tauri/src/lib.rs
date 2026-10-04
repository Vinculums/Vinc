//! Vinc 셸의 Rust 코어(Tauri) — 창·세션·수명주기 · 로컬 파일 다이얼로그 · **사이드카 spawn**.
//!
//! 아키텍처 계약([[vinc.arch]]): **제어 = Tauri IPC**(이 파일의 `#[command]`),
//! **데이터 = localhost HTTP**(프론트엔드가 사이드카를 직접 호출). 여기서는
//! 데이터 왕복을 하지 않는다 — 사이드카를 띄우고 그 접속 정보(host/port/token)를
//! 프론트엔드에 넘겨줄 뿐이다.
//!
//! 사이드카는 Python(`api/cli.py serve`)이다. 셸이 부모로서 `--parent-pid` 를
//! 넘겨 heartbeat 감독을 걸고(E6), 프로세스 종료 시 사이드카가 clean-shutdown 한다.

use std::io::{BufRead, BufReader};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use serde::{Deserialize, Serialize};
use tauri::{Manager, State};

/// 사이드카 접속 정보 — 프론트엔드가 `Authorization: Bearer {token}` 로 HTTP 호출.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct SidecarInfo {
    pub host: String,
    pub port: u16,
    pub token: String,
    #[serde(default)]
    pub driver: Option<String>,
}

/// 사이드카 프로세스 핸들 + 접속 정보를 보관하는 셸 상태.
#[derive(Default)]
pub struct SidecarState {
    child: Mutex<Option<Child>>,
    info: Mutex<Option<SidecarInfo>>,
}

/// Python 실행기/레포 루트는 환경변수로 오버라이드 가능(개발·배포 유연성).
fn python_bin() -> String {
    std::env::var("VINC_PYTHON").unwrap_or_else(|_| "python3".to_string())
}

/// 사이드카 CWD(= Vinc 레포 루트). 미지정 시 셸 실행 경로 기준 상위로 추정.
fn vinc_root() -> String {
    std::env::var("VINC_ROOT").unwrap_or_else(|_| "..".to_string())
}

/// 사이드카 기동 — `api/cli.py serve` 를 띄우고 첫 stdout JSON 줄({host,port,token})을
/// 파싱해 상태에 보관한다(§P2). 이미 기동돼 있으면 기존 정보를 돌려준다.
#[tauri::command]
fn start_sidecar(state: State<'_, SidecarState>) -> Result<SidecarInfo, String> {
    {
        let info = state.info.lock().unwrap();
        if let Some(i) = info.as_ref() {
            return Ok(i.clone());
        }
    }

    let parent_pid = std::process::id();
    let mut child = Command::new(python_bin())
        .current_dir(vinc_root())
        .args([
            "-m",
            "api.cli",
            // 개발 셸도 계정 설정과 무관하게 로컬 사이드카를 관리한다.
            "--target",
            "local",
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            "0",
            "--parent-pid",
            &parent_pid.to_string(),
        ])
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|e| format!("사이드카 기동 실패: {e}"))?;

    // 사이드카는 기동 정보를 stdout 1줄 JSON 으로 낸다(server.serve → print_info).
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "사이드카 stdout 캡처 실패".to_string())?;
    let mut reader = BufReader::new(stdout);
    let mut line = String::new();
    reader
        .read_line(&mut line)
        .map_err(|e| format!("사이드카 기동 정보 읽기 실패: {e}"))?;

    let info: SidecarInfo = serde_json::from_str(line.trim())
        .map_err(|e| format!("사이드카 기동 정보 파싱 실패: {e} (원문: {line})"))?;

    *state.child.lock().unwrap() = Some(child);
    *state.info.lock().unwrap() = Some(info.clone());
    Ok(info)
}

/// 현재 사이드카 접속 정보(미기동 시 오류).
#[tauri::command]
fn sidecar_info(state: State<'_, SidecarState>) -> Result<SidecarInfo, String> {
    state
        .info
        .lock()
        .unwrap()
        .clone()
        .ok_or_else(|| "사이드카 미기동".to_string())
}

/// 로컬 로그인(F4) — 패스프레이즈를 로컬 자격 파일과 대조. 네트워크 왕복 없음.
/// SSoT 인 Python `auth.verify_passphrase` 를 CLI(`verify`)로 재사용한다.
/// 패스프레이즈는 argv 노출을 피해 stdin 으로 전달한다.
#[tauri::command]
fn session_login(passphrase: String, cred_path: String) -> Result<bool, String> {
    run_cred_cmd("verify", &passphrase, &cred_path, &[])
}

/// 온보딩(F4) — 로컬 자격 생성(argon2id). 최초 1회.
#[tauri::command]
fn onboard(passphrase: String, cred_path: String) -> Result<bool, String> {
    run_cred_cmd("onboard", &passphrase, &cred_path, &["--force"])
}

/// `api/cli.py {verify|onboard} --profile <cred>` 실행, 패스프레이즈는 stdin 주입.
/// 종료코드 0 → true. (verify: 일치, onboard: 생성 성공)
fn run_cred_cmd(sub: &str, passphrase: &str, cred_path: &str, extra: &[&str]) -> Result<bool, String> {
    use std::io::Write;
    let mut args = vec!["-m", "api.cli", sub, "--profile", cred_path, "--passphrase", "-"];
    args.extend_from_slice(extra);
    let mut child = Command::new(python_bin())
        .current_dir(vinc_root())
        .args(&args)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|e| format!("{sub} 기동 실패: {e}"))?;
    if let Some(mut stdin) = child.stdin.take() {
        let _ = stdin.write_all(passphrase.as_bytes());
    }
    let status = child
        .wait()
        .map_err(|e| format!("{sub} 대기 실패: {e}"))?;
    Ok(status.success())
}

/// 사이드카를 명시 종료(창 닫힘·로그아웃 시). heartbeat 로도 정리되지만 즉시 회수용.
#[tauri::command]
fn stop_sidecar(state: State<'_, SidecarState>) -> Result<(), String> {
    if let Some(mut child) = state.child.lock().unwrap().take() {
        let _ = child.kill();
    }
    *state.info.lock().unwrap() = None;
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_shell::init())
        .manage(SidecarState::default())
        .invoke_handler(tauri::generate_handler![
            start_sidecar,
            sidecar_info,
            session_login,
            onboard,
            stop_sidecar
        ])
        .on_window_event(|window, event| {
            // 창 종료 시 사이드카 회수(E6 — 부모 사망 시 clean-shutdown 도 있지만 즉시 정리).
            if let tauri::WindowEvent::Destroyed = event {
                if let Some(state) = window.app_handle().try_state::<SidecarState>() {
                    if let Some(mut child) = state.child.lock().unwrap().take() {
                        let _ = child.kill();
                    }
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("Vinc 셸 기동 실패");
}
