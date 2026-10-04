// Vinc Tauri shell (P4 — D43) — control plane only.
// Spawns the bundled sidecar (externalBin `vinc`) with a per-session
// random token, and hands the connection info (port + token) to the webview via
// the `sidecar_conn` command — the frontend invokes it on startup and configures
// its HTTP client. (A `win.eval` localStorage write is kept as a best-effort
// fallback; it is timing-fragile, which is why the command is the source of truth.)
// All data flows over localhost HTTP (never IPC) — see Vinc-docs vinc/ARCHITECTURE.md.
// Data home defaults to ~/.vinc (D43).
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod cli_link;
mod secret;
mod session_file;

use tauri::{Emitter, Manager, RunEvent};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

const SIDECAR_PORT: u16 = 8787; // 폴백 전용 — 실제 포트는 pick_free_port() 로 동적 배정.

struct Sidecar(std::sync::Mutex<Option<CommandChild>>);

/// 끝난 사이드카의 pid. 재기동(`sidecar_restart`)이 옛 프로세스가 스토어를 닫고 나갔는지
/// 이것으로 안다. 이벤트 스트림이 `Terminated` 를 주거나 닫히면 넣는다.
struct SidecarExited(std::sync::Mutex<std::collections::HashSet<u32>>);

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

/// 셸이 띄운 사이드카가 아직 살아 있는가. 로그인 화면이 "끝났으니 다시 띄운다"와 "아직
/// 뜨는 중이니 기다린다"를 이것으로 가른다. `sidecar_status` 의 `terminated:` 줄로는 못
/// 가른다: 그 버퍼는 24줄에서 끊겨 종료 줄이 빠질 수 있다.
#[tauri::command]
fn sidecar_alive(app: tauri::AppHandle) -> bool {
    let Some(pid) = app.state::<Sidecar>().0.lock().unwrap().as_ref().map(|c| c.pid()) else {
        return false;
    };
    !app.state::<SidecarExited>().0.lock().unwrap().contains(&pid)
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
        .sidecar("vinc")
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

/// Settings→Connections: 터미널에서 `vinc` 를 부를 수 있는지 본다(B 2단계).
/// deb·rpm 은 패키지가 이미 `/usr/bin/vinc` 를 깔아 두므로 이 화면이 할 일이 없고,
/// macOS·AppImage 는 설치 훅이 없어 여기서 링크를 건다.
#[tauri::command]
fn cli_link_status() -> cli_link::Status {
    #[cfg(any(unix, windows))]
    {
        cli_link::status()
    }
    #[cfg(not(any(unix, windows)))]
    {
        cli_link::unsupported()
    }
}

#[tauri::command]
fn cli_link_install() -> Result<cli_link::Status, String> {
    #[cfg(any(unix, windows))]
    {
        cli_link::install()
    }
    #[cfg(not(any(unix, windows)))]
    {
        Err("not supported on this platform".into())
    }
}

#[tauri::command]
fn cli_link_remove() -> Result<cli_link::Status, String> {
    #[cfg(any(unix, windows))]
    {
        cli_link::remove()
    }
    #[cfg(not(any(unix, windows)))]
    {
        Err("not supported on this platform".into())
    }
}

// ── `.vincpack` 파일 연결 (Q1, 2026-08-07) ──────────────────────────────────
// `tauri.conf.json` 의 `bundle.fileAssociations` 에 `.vincpack` 이 선언돼 출하됐는데
// **argv 를 읽는 코드가 어디에도 없었다.** single-instance 콜백 시그니처가
// `|app, _args, _cwd|` 로 args 를 버렸고 다른 어디에도 경로 해석 분기가 없었다.
// 즉 더블클릭하면 창만 뜨고 팩은 안 열렸다 — 선언만 있고 동작이 없는 상태.
// 이미 이 확장자로 공유가 이루어지고 있어(커뮤니티 갤러리) 사용자에겐 "깨진 연결"이다.
//
// 설계: **바이트를 러스트가 읽어 프론트에 넘긴다.** 경로만 넘기면 프론트가 임의 경로를
// 읽을 권한이 필요해지는데(fs 스코프 확대), 그럴 이유가 없다. 프론트는 받은 바이트를
// 기존 `fromBytes()` 에 그대로 흘려 **드롭한 파일과 완전히 같은 경로**를 탄다
// (zip·JSON 판별, 검증, 임포트 시트까지 재사용 — 새 파서를 만들지 않는다).
//
// ⚠ 경합이 있다: 최초 실행의 argv 는 프론트가 리스너를 걸기 **전에** 도착한다.
// 그래서 이벤트만으로는 첫 더블클릭이 조용히 유실된다. 큐에 쌓아 두고 프론트가
// 준비되면 `take_pending_files` 로 **가져가게** 한다(이벤트는 2회차 실행용).
#[derive(Default)]
struct PendingOpen(std::sync::Mutex<Vec<String>>);

#[derive(serde::Serialize, Clone)]
struct OpenedFile {
    name: String,
    /// 바이트를 그대로 실으면 JSON 배열이 되어 5MB 팩이 15MB 로 부푼다.
    /// 프론트가 `atob` 로 되돌린다.
    b64: String,
}

/// 의존을 늘리지 않으려고 표준 base64 를 직접 짠다(인코딩만 필요하다).
fn b64(bytes: &[u8]) -> String {
    const T: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity((bytes.len() + 2) / 3 * 4);
    for c in bytes.chunks(3) {
        let b = [c[0], *c.get(1).unwrap_or(&0), *c.get(2).unwrap_or(&0)];
        let n = ((b[0] as u32) << 16) | ((b[1] as u32) << 8) | b[2] as u32;
        out.push(T[(n >> 18 & 63) as usize] as char);
        out.push(T[(n >> 12 & 63) as usize] as char);
        out.push(if c.len() > 1 { T[(n >> 6 & 63) as usize] as char } else { '=' });
        out.push(if c.len() > 2 { T[(n & 63) as usize] as char } else { '=' });
    }
    out
}

// ── CLI 디스패치 (`vinc <동사> …`) ──────────────────────────────────────────
// ⚠ 이 절의 전제가 2026-09-18 에 뒤집혔다(VIN-72). 그 전에는 **이 바이너리**가
// `/usr/bin/vinc` 였고 엔진이 `/usr/bin/vinc-sidecar` 였다(v0.8.2 deb 실측: 앱 23MB ·
// 엔진 37MB). 그래서 문서가 안내하던 `vinc mcp install --client desktop` 이 "명령
// 없음"이 아니라 **창만 띄우고 끝났고** — argv 가 `openable_from_args` 에서 조용히
// 버려졌다 — 조용한 오작동은 없는 명령보다 나쁘다는 이유로 이 디스패처를 달았다.
//
// 이제는 **엔진이 `vinc` 를 갖고 이 바이너리가 `vinc-app` 으로 비켰다**
// (`tauri.conf.json` 의 `mainBinaryName`). 그래서 리눅스에서 `vinc <동사>` 는 이 코드를
// 거치지 않고 엔진에 바로 닿는다.
//
// 그런데도 이 디스패처는 **남긴다.** macOS 와 AppImage 는 패키지 설치 훅이 없어
// `cli_link` 가 앱 바이너리로 링크를 걸고, 이미 깔린 설치본에는 옛 배치가 그대로
// 남아 있기 때문이다. 지우면 그 두 경우에서 다시 창만 뜬다.
//
// 정본은 그래프의 `vinc.plan.vinc-command-on-path`.

/// 사이드카 CLI 의 최상위 하위 명령 — `api/cli.py` 의 `sub.add_parser(...)` 와 1:1.
/// ⚠ 여기에 없는 첫 인자는 **전부 창을 여는 쪽**으로 간다. 새 하위 명령을 CLI 에
/// 추가하면 이 배열도 같이 늘려야 한다(안 늘리면 그 명령만 창이 뜬다).
/// `--help`·`-h`·`--version`·`-V` 도 창 없이 번들 CLI 가 직접 출력한다.
const CLI_VERBS: [&str; 39] = [
    "activate", "backup", "brief", "call", "conventions", "decide", "delete", "diagnose",
    "doc", "domain-backfill", "domains", "edge", "export", "graph", "graph-md", "import", "ingest",
    "learner", "mcp", "merge", "migrate-dim", "node", "onboard", "path", "pin", "plan",
    "recall", "refs", "search", "serve", "share", "tag", "ties", "touch", "whoami", "--help", "-h",
    "--version", "-V",
];

/// argv 가 CLI 호출이면 사이드카에 넘길 인자를, 아니면 `None`(= 창을 연다).
/// argv[0] 은 실행 파일이라 건너뛴다. macOS 가 GUI 실행에 붙이는 `-psn_…` 이나
/// `.vincpack` 경로는 동사 목록에 없으므로 자연히 창으로 간다.
fn cli_forward<I: IntoIterator<Item = String>>(args: I) -> Option<Vec<String>> {
    let all: Vec<String> = args.into_iter().collect();
    let mut index = 1;
    let mut global_option = false;
    // 전역 출력 옵션 다음의 동사가 CLI 이면 원래 argv 순서 그대로 엔진에 보낸다.
    while index < all.len() {
        let token = all[index].as_str();
        match token {
            // 값을 받는 전역 옵션: `--target account search …` 도 CLI 로 보낸다.
            "--format" | "--target" | "--space" => { index += 2; global_option = true; },
            "--json" | "--debug" => { index += 1; global_option = true; },
            _ if token.starts_with("--format=") || token.starts_with("--target=")
                || token.starts_with("--space=") => { index += 1; global_option = true; },
            _ => break,
        }
    }
    if index >= all.len() {
        return global_option.then(|| all[1..].to_vec());
    }
    if !CLI_VERBS.contains(&all[index].as_str()) {
        return None;
    }
    Some(all[1..].to_vec())
}

/// 번들 엔진의 경로 — Tauri externalBin 은 **이 바이너리 옆**에 놓인다.
/// 개발 트리에는 타깃 트리플이 붙은 이름(`vinc-x86_64-…`)으로 있어 둘 다 본다.
///
/// ⚠ 접두사로 찾을 때 **자기 자신을 집으면 앱이 앱을 부른다.** 엔진이 `vinc` 라는
/// 짧은 이름을 가져가면서(VIN-72) 앱 바이너리는 `vinc-app` 이 됐는데, 둘 다 `vinc`
/// 로 시작한다. 그래서 현재 실행 파일과 `vinc-app` 은 후보에서 먼저 걷어낸다.
/// 옛 이름(`vinc-sidecar`)도 계속 찾는다 — 이미 깔린 설치본과 그 이름으로 등록된
/// MCP 호스트 설정이 남아 있다.
fn sidecar_binary() -> Option<std::path::PathBuf> {
    let exe = std::env::current_exe().ok()?;
    let dir = exe.parent()?.to_path_buf();
    let exe_name = exe.file_name().and_then(|s| s.to_str()).unwrap_or("").to_string();
    for name in ["vinc", "vinc-sidecar"] {
        let exact = dir.join(if cfg!(windows) {
            format!("{name}.exe")
        } else {
            name.to_string()
        });
        if exact.is_file() && exact != exe {
            return Some(exact);
        }
    }
    // 트리플이 붙은 개발 트리 이름. 앱 자신과 `vinc-app` 은 제외한다.
    let usable = |n: &str| {
        n != exe_name
            && !n.starts_with("vinc-app")
            && (n.starts_with("vinc-sidecar") || n.starts_with("vinc-"))
    };
    std::fs::read_dir(&dir)
        .ok()?
        .flatten()
        .map(|e| e.path())
        .find(|p| {
            p.is_file()
                && p.file_name()
                    .and_then(|s| s.to_str())
                    .map_or(false, usable)
        })
}

/// CLI 로 불렸다 — 사이드카를 그대로 실행하고 **종료코드를 넘긴 뒤** 끝낸다.
/// stdio 는 상속한다(파이프·리다이렉트가 셸에서 기대대로 동작하도록).
/// ⚠ Windows 는 릴리스 빌드가 GUI 서브시스템이라 콘솔에서 부르면 자식 출력이 보이지
/// 않는다. 그쪽은 PATH 등록 작업과 함께 실제 러너에서 검증하며 다룬다(계획 문서 §3).
fn run_cli(fwd: Vec<String>) -> ! {
    // Windows 릴리스 빌드는 GUI 서브시스템이라 콘솔이 안 붙는다 — 그대로 두면 엔진
    // 출력이 어디에도 안 보인다. 부모(셸)의 콘솔에 붙여 stdout·stderr 가 그 창으로
    // 흐르게 한다. 실패는 무시한다(콘솔 없이 실행된 경우 = GUI 에서 부른 경우).
    #[cfg(windows)]
    unsafe {
        const ATTACH_PARENT_PROCESS: u32 = 0xFFFF_FFFF;
        #[link(name = "kernel32")]
        extern "system" {
            fn AttachConsole(dwProcessId: u32) -> i32;
        }
        AttachConsole(ATTACH_PARENT_PROCESS);
    }
    let Some(bin) = sidecar_binary() else {
        eprintln!("vinc: 번들 엔진(vinc)을 찾지 못했다 — 설치본이 온전한지 확인할 것.");
        std::process::exit(127);
    };
    match std::process::Command::new(&bin).args(&fwd).status() {
        Ok(st) => std::process::exit(st.code().unwrap_or(1)),
        Err(e) => {
            eprintln!("vinc: 엔진을 실행하지 못했다 ({}): {e}", bin.display());
            std::process::exit(126);
        }
    }
}

/// argv 에서 **열 수 있는 파일만** 고른다. 플래그·포트 인자·존재하지 않는 경로는
/// 버린다 — argv[0] 은 실행 파일이므로 항상 건너뛴다.
fn openable_from_args<I: IntoIterator<Item = String>>(args: I) -> Vec<String> {
    args.into_iter()
        .skip(1)
        .filter(|a| !a.starts_with('-'))
        .filter(|a| {
            let low = a.to_lowercase();
            (low.ends_with(".vincpack") || low.ends_with(".json"))
                && std::path::Path::new(a).is_file()
        })
        .collect()
}

/// 프론트가 준비되면 큐를 **비우면서** 가져간다(중복 임포트 방지).
// ── 계정 키 보관(감사 M8) ────────────────────────────────────────────────────
// 이름은 프런트가 정한다(`vinc.sync.key` 등) — 러스트는 어느 값이 무엇인지 모른다.
// ⚠ 값을 로그에 싣지 않는다. 오류 문자열만 넘긴다.
#[tauri::command]
fn secret_get(name: String) -> Result<Option<String>, String> {
    secret::get(&name)
}

#[tauri::command]
fn secret_set(name: String, value: String) -> Result<(), String> {
    secret::set(&name, &value)
}

#[tauri::command]
fn secret_delete(name: String) -> Result<(), String> {
    secret::delete(&name)
}

/// 이 기기에 OS 저장소가 실제로 되는가. 프런트가 이 값으로 폴백을 정하고 화면에 적는다.
#[tauri::command]
fn secret_available() -> bool {
    secret::available()
}

#[tauri::command]
fn take_pending_files(state: tauri::State<'_, PendingOpen>) -> Vec<OpenedFile> {
    let paths: Vec<String> = state.0.lock().map(|mut v| std::mem::take(&mut *v)).unwrap_or_default();
    paths
        .iter()
        .filter_map(|p| match std::fs::read(p) {
            Ok(bytes) => {
                log::info!(target: "open", "file association: {p} ({} bytes)", bytes.len());
                Some(OpenedFile {
                    name: std::path::Path::new(p)
                        .file_name()
                        .map(|s| s.to_string_lossy().into_owned())
                        .unwrap_or_else(|| p.clone()),
                    b64: b64(&bytes),
                })
            }
            Err(e) => {
                // 조용히 버리지 않는다 — 더블클릭했는데 아무 일도 안 일어나는 것이
                // 정확히 이 버그의 증상이었다.
                log::warn!(target: "open", "file association: {p} 읽기 실패: {e}");
                None
            }
        })
        .collect()
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
    let body = serde_json::json!({
        "port": port, "token": token, "pid": std::process::id(), "v": "0.1.9"
    });
    let wrote = session_file::write_private(&path, body.to_string().as_bytes());
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

/// 사이드카를 띄우고 수명 이벤트를 기록한다. 기동과 재기동이 같은 길을 탄다.
///
/// 셸 PID 를 넘겨 heartbeat 감독을 건다(E6) — 셸이 크래시·강제종료로 죽어
/// RunEvent::Exit 가 안 불려도 사이드카가 ~1s 내 자가종료(고아 근절).
/// (Windows-안전 조회는 사이드카 server._pid_alive 참조.)
///
/// 시작 시 이미지명 일괄 taskkill(/IM)로 "고아 회수"는 하지 않는다: 그러면
/// Settings→MCP 로 Claude 클라이언트에 등록된 브리지(`vinc.exe mcp serve`)
/// 와 그 자식까지 같은 이미지명이라 죽여 실행 중 MCP 세션을 끊는다. 누수는
/// single-instance(중복 셸 차단)+parent-pid heartbeat(크래시 고아 자가종료)+
/// 종료 시 트리-kill 로 대신 막고, 구버전 잔존 고아는 설치 시 NSIS 훅이 정리한다.
fn spawn_sidecar(app: &tauri::AppHandle, port: u16, token: &str) -> Result<u32, String> {
    let parent_pid = std::process::id().to_string();
    let port_arg = port.to_string();
    // 동적 포트(Conn 과 동일 값) — 옛 사이드카가 고정 포트를 물고 있어도 무충돌.
    // `--data` 는 넘기지 않는다: 저장 폴더는 사이드카가 `~/.vinc/config.json` 에서 읽는다
    // (VIN-130). 넘기면 설정 화면이 바꾼 폴더가 이 프로세스에 영영 안 먹는다.
    let sidecar = app
        .shell()
        .sidecar("vinc")
        .map_err(|e| e.to_string())?
        .args([
            // 앱의 관리 사이드카는 계정 모드에서도 이 기기의 저장소를 연다.
            "--target",
            "local",
            "serve",
            "--port",
            &port_arg,
            "--parent-pid",
            &parent_pid,
        ])
        // 세션 비밀값을 프로세스 명령행에 남기지 않는다.
        .env("VINC_SIDECAR_TOKEN", token)
        // 업데이트 뒤 첫 기동에서 검색 색인을 Chunk.lex 로 옮긴다(VIN-271·VIN-272). 사이드카
        // 기본은 defer(서비스 중에 안 옮김)이고, 데스크톱만 이 값으로 기동 뒤에 옮긴다. 그동안
        // /health 가 migrating 을 실어 앱이 안내를 띄운다.
        .env("VINC_LEX_BACKFILL", "background");
    let (mut events, child) = sidecar.spawn().map_err(|e| {
        // spawn 자체가 실패하면 CommandEvent 조차 안 온다 → 여기서 안 남기면 증거 0.
        log::error!(target: "sidecar", "spawn failed: {e}");
        e.to_string()
    })?;
    let pid = child.pid();
    log::info!(
        target: "sidecar",
        "spawned v{} pid={} port={} parent={}",
        app.package_info().version, pid, port, parent_pid,
    );
    *app.state::<Sidecar>().0.lock().unwrap() = Some(child);
    // MCP 브리지가 이 사이드카를 찾아 붙도록 세션 파일 기록(A1.9).
    write_session(app, port, token);
    // 사이드카 수명 이벤트를 흘려보내지 않고 기록한다 — 프론트가
    // `sidecar_status` 로 읽어 진단에 싣는다(조용한 즉사 방지).
    let handle = app.clone();
    tauri::async_runtime::spawn(async move {
        while let Some(ev) = events.recv().await {
            let line = match ev {
                CommandEvent::Stderr(b) => {
                    let s = String::from_utf8_lossy(&b).trim().to_string();
                    if s.is_empty() { continue; }
                    format!("stderr: {}", s.chars().take(200).collect::<String>())
                }
                // stdout 도 남긴다. 여태 `_ => continue` 가 이것을 통째로
                // 버려서, 사이드카가 stdout 으로 낸 것은 **어디에도** 안
                // 남았다(파일 로그에도, 진단 문자열에도). 기동 실패를 조사할
                // 때 stderr 만 있고 stdout 이 없으면 반쪽이다.
                CommandEvent::Stdout(b) => {
                    let s = String::from_utf8_lossy(&b).trim().to_string();
                    if s.is_empty() { continue; }
                    format!("stdout: {}", s.chars().take(200).collect::<String>())
                }
                CommandEvent::Error(e) => format!("spawn error: {e}"),
                CommandEvent::Terminated(p) => {
                    handle.state::<SidecarExited>().0.lock().unwrap().insert(pid);
                    format!("terminated: code={:?} signal={:?}", p.code, p.signal)
                }
                _ => continue,
            };
            handle.state::<SidecarLog>().push(line);
        }
        // 스트림이 닫혔다 = 프로세스가 끝났다(`Terminated` 를 못 받았어도).
        handle.state::<SidecarExited>().0.lock().unwrap().insert(pid);
    });
    Ok(pid)
}

/// 사이드카에 스스로 닫으라고 부탁한다(`POST /shutdown`). 202 를 받았으면 true.
///
/// 죽이지 않고 부탁하는 이유: 그 길은 사이드카의 `close()` 를 지나 스토어를 닫는다.
/// 강제 종료는 다음 기동을 WAL 재생에 맡기는데, 허브를 세 번 세운 고장이 그 재생이었다.
/// HTTP 클라이언트 크레이트를 들이지 않으려고 루프백에 요청 한 줄을 직접 쓴다.
fn request_shutdown(port: u16, token: &str) -> bool {
    use std::io::{Read, Write};
    use std::time::Duration;
    let addr = std::net::SocketAddr::from(([127, 0, 0, 1], port));
    let Ok(mut s) = std::net::TcpStream::connect_timeout(&addr, Duration::from_secs(2)) else {
        return false;
    };
    let _ = s.set_read_timeout(Some(Duration::from_secs(5)));
    let req = format!(
        "POST /shutdown HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAuthorization: Bearer {token}\r\n\
         Content-Type: application/json\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{{}}"
    );
    if s.write_all(req.as_bytes()).is_err() {
        return false;
    }
    let mut buf = [0u8; 64];
    match s.read(&mut buf) {
        Ok(n) if n > 0 => status_is_accepted(&String::from_utf8_lossy(&buf[..n])),
        _ => false,
    }
}

/// 응답 첫 줄이 202 인가(`HTTP/1.x 202 ...`).
fn status_is_accepted(head: &str) -> bool {
    head.lines()
        .next()
        .and_then(|l| l.split_whitespace().nth(1))
        .map(|code| code == "202")
        .unwrap_or(false)
}

/// `pid` 가 끝났다는 기록을 `ms` 동안 기다린다.
fn wait_exit(app: &tauri::AppHandle, pid: u32, ms: u64) -> bool {
    let deadline = std::time::Instant::now() + std::time::Duration::from_millis(ms);
    loop {
        if app.state::<SidecarExited>().0.lock().unwrap().contains(&pid) {
            return true;
        }
        if std::time::Instant::now() >= deadline {
            return false;
        }
        std::thread::sleep(std::time::Duration::from_millis(100));
    }
}

/// 저장 폴더를 바꾼 뒤(VIN-130) 사이드카를 다시 띄운다. 포트와 토큰은 그대로라 프론트의
/// 접속 정보(`sidecar_conn`)도 세션 파일도 그대로 유효하다.
///
/// 옛 프로세스가 **끝난 것을 확인한 뒤에** 띄운다. 스토어는 한 프로세스만 연다: 겹치면 새
/// 사이드카가 락에 걸려 "스토어가 안 열린다"로 뜬다. 부탁해도 20초 안에 안 끝나면 그때
/// 강제로 끝낸다.
#[tauri::command]
async fn sidecar_restart(app: tauri::AppHandle) -> Result<u32, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let (port, token) = {
            let c = app.state::<Conn>();
            (c.port, c.token.clone())
        };
        let old = app.state::<Sidecar>().0.lock().unwrap().take();
        if let Some(child) = old {
            let pid = child.pid();
            let asked = request_shutdown(port, &token);
            log::info!(target: "sidecar", "restart: pid={pid} shutdown requested={asked}");
            if !(asked && wait_exit(&app, pid, 20_000)) {
                log::warn!(target: "sidecar", "restart: pid={pid} did not stop, killing");
                #[cfg(windows)]
                kill_sidecar_tree(pid);
                let _ = child.kill();
                if !wait_exit(&app, pid, 5_000) {
                    return Err(format!("the previous sidecar (pid {pid}) did not exit"));
                }
            }
        }
        // 진단 버퍼는 새 프로세스의 것만 담는다(24줄 상한이라 옛 줄이 새 사인을 가린다).
        app.state::<SidecarLog>().0.lock().unwrap().clear();
        spawn_sidecar(&app, port, &token)
    })
    .await
    .map_err(|e| e.to_string())?
}

fn main() {
    // CLI 호출이면 여기서 갈린다 — Tauri 를 세우기 전에, 창을 열지 않고.
    if let Some(fwd) = cli_forward(std::env::args()) {
        run_cli(fwd);
    }
    let token = session_token();
    let port = pick_free_port();
    let mut builder = tauri::Builder::default();
    // 단일 인스턴스 가드는 **가장 먼저** 등록(다른 플러그인 개입 전 선점). 두 번째 실행은
    // 기존 창을 포커스하고 즉시 종료 → 사이드카 중복 spawn·누수 근절.
    #[cfg(desktop)]
    {
        builder = builder.plugin(tauri_plugin_single_instance::init(|app, args, _cwd| {
            // 2회차 실행의 argv 가 여기로 온다 — `.vincpack` 더블클릭이 대표적이다.
            // 여태 `_args` 로 버려서 창만 뜨고 팩은 안 열렸다(Q1).
            let files = openable_from_args(args);
            if !files.is_empty() {
                if let Some(st) = app.try_state::<PendingOpen>() {
                    if let Ok(mut q) = st.0.lock() {
                        q.extend(files);
                    }
                }
                // 이쪽은 프론트가 이미 살아 있으므로 이벤트로 깨운다. 페이로드는
                // 싣지 않는다 — 본문은 `take_pending_files` 한 문으로만 나간다.
                let _ = app.emit("vinc://open-files", ());
            }
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
        .manage(SidecarExited(std::sync::Mutex::new(std::collections::HashSet::new())))
        .manage(SidecarLog(std::sync::Mutex::new(Vec::new())))
        .manage(Conn {
            port,
            token: token.clone(),
        })
        // 최초 실행의 argv 는 프론트 리스너가 걸리기 전에 도착한다 — 큐에 먼저 담는다.
        .manage(PendingOpen(std::sync::Mutex::new(openable_from_args(
            std::env::args().collect::<Vec<_>>(),
        ))))
        .invoke_handler(tauri::generate_handler![
            sidecar_conn, sidecar_status, sidecar_alive, sidecar_restart, mcp_install, mcp_uninstall,
            take_pending_files,
            cli_link_status, cli_link_install, cli_link_remove,
            secret_get, secret_set, secret_delete, secret_available])
        .setup(move |app| {
            spawn_sidecar(app.handle(), port, &token)?;
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
                // 먼저 닫아 달라고 부탁한다. 그 길이 스토어를 닫고(체크포인트) 나가서 다음
                // 기동이 WAL 을 재생하지 않는다. 여태 여기서 곧장 죽여서 종료할 때마다 WAL 이
                // 남았고, 그 재생이 엔진을 죽이면 다음 기동에서 로그인까지 막혔다.
                // 근거: [[record:a-wal-that-crashes-the-engine-takes-the-desktop-sign-in-down-2026-09-27]]
                let pid = child.pid();
                let (port, token) = {
                    let c = app.state::<Conn>();
                    (c.port, c.token.clone())
                };
                // 창은 이미 닫혔다. 5초 안에 안 끝나면 그때 죽인다(재기동의 20초보다 짧게).
                if !(request_shutdown(port, &token) && wait_exit(app, pid, 5_000)) {
                    // Windows: 부트로더만 죽으면 파이썬 자식이 고아로 .exe 락을 유지 →
                    // 트리째 강제 종료(child.kill 은 폴백).
                    #[cfg(windows)]
                    kill_sidecar_tree(pid);
                    let _ = child.kill();
                }
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    /// `.vincpack` 더블클릭이 사문이던 원인은 argv 를 아무도 안 읽은 것이었다(Q1).
    /// 여기서 고정하는 계약은 "무엇을 열 것인가" 하나다 — 잘못 고르면 앱이 자기
    /// 실행 인자(포트·플래그)를 팩으로 열려 든다.
    #[test]
    fn argv_에서_열_수_있는_파일만_고른다() {
        let dir = std::env::temp_dir().join("vinc-argv-test");
        let _ = std::fs::create_dir_all(&dir);
        let pack = dir.join("shared.vincpack");
        let json = dir.join("backup.json");
        let other = dir.join("notes.md");
        for p in [&pack, &json, &other] {
            std::fs::write(p, b"x").unwrap();
        }
        let missing = dir.join("gone.vincpack");
        let _ = std::fs::remove_file(&missing);

        let argv = vec![
            "vinc.exe".to_string(),          // argv[0] 은 실행 파일 — 항상 건너뛴다
            "--port".to_string(),            // 플래그
            "8787".to_string(),              // 플래그 값(확장자 없음)
            pack.to_string_lossy().into_owned(),
            json.to_string_lossy().into_owned(),
            other.to_string_lossy().into_owned(),      // 지원 확장자 아님
            missing.to_string_lossy().into_owned(),    // 존재하지 않음
        ];
        let got = openable_from_args(argv);
        assert_eq!(got.len(), 2, "고른 것: {got:?}");
        assert!(got[0].ends_with("shared.vincpack"));
        assert!(got[1].ends_with("backup.json"));
    }

    #[test]
    fn argv_가_실행파일뿐이면_아무것도_안_연다() {
        assert!(openable_from_args(vec!["vinc.exe".to_string()]).is_empty());
    }

    /// 리눅스 패키지에서 이 바이너리의 이름은 `vinc` 다. 그래서 `vinc mcp install …`
    /// 이 **창을 열면 안 된다** — 그게 이 디스패처를 만든 이유다.
    #[test]
    fn cli_동사는_사이드카로_넘어간다() {
        let argv = vec![
            "/usr/bin/vinc".to_string(),
            "mcp".to_string(),
            "install".to_string(),
            "--client".to_string(),
            "desktop".to_string(),
        ];
        let fwd = cli_forward(argv).expect("동사면 넘겨야 한다");
        assert_eq!(fwd, vec!["mcp", "install", "--client", "desktop"]);
    }

    /// 반대쪽 절반: 창을 열어야 하는 입력은 여전히 창을 연다. 파일 연결(Q1)이
    /// 이 디스패처 때문에 다시 사문이 되면 안 된다.
    #[test]
    fn 창을_열어야_하는_argv_는_넘기지_않는다() {
        let cases: Vec<Vec<String>> = vec![
            vec!["/usr/bin/vinc".into()],                                   // 인자 없음
            vec!["/usr/bin/vinc".into(), "/home/u/shared.vincpack".into()], // 더블클릭
            vec!["/usr/bin/vinc".into(), "-psn_0_12345".into()],            // macOS GUI 실행
            vec!["/usr/bin/vinc".into(), "instal".into()],                  // 오타 = 동사 아님
            vec!["/usr/bin/vinc".into(), "--port".into(), "8787".into()],   // 앱 플래그
        ];
        for argv in cases {
            assert!(cli_forward(argv.clone()).is_none(), "창을 열어야 한다: {argv:?}");
        }
    }

    /// 동사 뒤의 인자는 **그대로** 간다. 여기서 순서나 개수가 바뀌면 CLI 가 다른 일을 한다.
    #[test]
    fn 동사_뒤_인자는_변형없이_전달된다() {
        let argv = vec![
            "vinc".to_string(),
            "search".to_string(),
            "--query".to_string(),
            "vector search".to_string(),
            "--limit".to_string(),
            "5".to_string(),
        ];
        let fwd = cli_forward(argv).unwrap();
        assert_eq!(fwd, vec!["search", "--query", "vector search", "--limit", "5"]);
    }

    /// `--help` 는 동사 자리에서 받는다 — argparse 가 사용법을 찍는다.
    #[test]
    fn help_는_cli_로_간다() {
        assert!(cli_forward(vec!["vinc".to_string(), "--help".to_string()]).is_some());
        assert!(cli_forward(vec!["vinc".to_string(), "-h".to_string()]).is_some());
    }

    /// 버전은 대상·키·스토어 설정을 읽기 전에 번들 CLI 에서 끝나야 한다.
    /// 전역 옵션 앞뒤 모두 원래 순서를 보존해 CLI 의 조기 종료에 맡긴다.
    #[test]
    fn version_별칭은_설정과_무관하게_cli_로_간다() {
        for alias in ["--version", "-V"] {
            for prefix in [
                vec![],
                vec!["--target", "invalid", "--space", "missing"],
                vec!["--target=account", "--format=json", "--debug"],
            ] {
                let mut argv = vec!["vinc-app"];
                argv.extend(prefix);
                argv.push(alias);
                let args = argv.iter().map(|s| s.to_string()).collect::<Vec<_>>();
                assert_eq!(cli_forward(args.clone()), Some(args[1..].to_vec()), "{argv:?}");
            }
            let args = ["vinc-app", alias, "--target", "invalid"]
                .into_iter().map(str::to_string).collect::<Vec<_>>();
            assert_eq!(cli_forward(args.clone()), Some(args[1..].to_vec()));
        }
    }

    #[test]
    fn 전역_출력_옵션_뒤_동사도_cli_로_간다() {
        let args = vec!["vinc", "--format", "json", "doc", "ls"]
            .into_iter().map(str::to_string).collect::<Vec<_>>();
        assert_eq!(cli_forward(args), Some(vec!["--format", "json", "doc", "ls"]
            .into_iter().map(str::to_string).collect()));
        let missing = vec!["vinc", "--format", "json"]
            .into_iter().map(str::to_string).collect::<Vec<_>>();
        assert_eq!(cli_forward(missing), Some(vec!["--format", "json"]
            .into_iter().map(str::to_string).collect()));
    }

    #[test]
    fn 대상_스페이스_옵션_뒤_동사도_cli_로_간다() {
        for argv in [
            vec!["vinc", "--target", "account", "search", "--query", "x"],
            vec!["vinc", "--space", "personal", "domains"],
            vec!["vinc", "--target=local", "--format=json", "doc", "ls"],
        ] {
            let args = argv.iter().map(|s| s.to_string()).collect::<Vec<_>>();
            assert_eq!(cli_forward(args.clone()), Some(args[1..].to_vec()), "{argv:?}");
        }
    }

    /// 동사 목록이 사이드카 CLI 와 어긋나면 그 명령만 조용히 창을 연다.
    /// 목록 자체를 고정해 두어, `api/cli.py` 를 고칠 때 이 테스트가 같이 깨지게 한다.
    #[test]
    fn 동사_목록은_사이드카_cli_와_짝이다() {
        for v in ["mcp", "serve", "search", "doc", "import", "share", "graph-md", "onboard",
                  "brief", "migrate-dim", "whoami", "conventions", "node", "path", "ties",
                  "diagnose", "ingest", "delete", "edge", "merge", "call", "export", "refs",
                  "--help", "-h", "--version", "-V"] {
            assert!(CLI_VERBS.contains(&v), "{v} 가 목록에 없다");
        }
        assert!(!CLI_VERBS.contains(&"install"), "install 은 mcp 의 하위라 최상위가 아니다");
    }

    /// 재기동은 사이드카가 "닫겠다"(202)고 답했을 때만 기다린다. 401·409 를 정상 종료로
    /// 읽으면 안 끝나는 프로세스를 20초 기다린 뒤에야 끊는다.
    #[test]
    fn 재기동은_202_만_정상_종료로_본다() {
        assert!(status_is_accepted("HTTP/1.0 202 Accepted\r\nServer: x\r\n"));
        assert!(!status_is_accepted("HTTP/1.0 401 Unauthorized\r\n"));
        assert!(!status_is_accepted("HTTP/1.0 409 Conflict\r\n"));
        assert!(!status_is_accepted(""));
    }

    /// 바이트를 JSON 배열로 실으면 5MB 팩이 15MB 로 부푼다. base64 는 그래서 쓰는데,
    /// 손으로 짠 인코더라 패딩 경계(3n·3n+1·3n+2)를 고정해 둔다 — 여기가 틀리면
    /// 프론트의 `atob` 가 조용히 다른 바이트를 돌려준다.
    #[test]
    fn base64_가_패딩_경계에서_정확하다() {
        assert_eq!(b64(b""), "");
        assert_eq!(b64(b"f"), "Zg==");
        assert_eq!(b64(b"fo"), "Zm8=");
        assert_eq!(b64(b"foo"), "Zm9v");
        assert_eq!(b64(b"foob"), "Zm9vYg==");
        assert_eq!(b64(b"fooba"), "Zm9vYmE=");
        assert_eq!(b64(b"foobar"), "Zm9vYmFy");
        // zip(팩의 실제 형태)의 매직 바이트 — 비ASCII 경로도 밟는다.
        assert_eq!(b64(&[0x50, 0x4b, 0x03, 0x04]), "UEsDBA==");
    }
}
