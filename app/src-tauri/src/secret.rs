// 계정 키를 OS 비밀 저장소에 둔다 (감사 2026-08-18 M8).
//
// 배경: 회원 키(`vinc_sk_*`)가 웹뷰의 `localStorage` 에 **평문**으로 있었다. 그 파일은
// 웹뷰 프로파일 디렉터리에 그냥 눕고, 백업·동기화·검색에 그대로 걸린다. 그리고 웹뷰
// 안에서 도는 코드는 무엇이든 읽을 수 있다.
//
// ⚠ **이득의 크기를 부풀리지 말 것.** Windows 자격 증명 관리자와 리눅스 secret-service
// 는 **같은 사용자로 도는 프로세스에게 그냥 내준다.** 그러니 "같은 OS 사용자" 위협은
// 여기서 거의 안 막힌다. 실제로 닫히는 것은 둘이다:
//   · 평문이 디스크에 눕지 않는다(백업·동기화·grep 에 안 걸린다)
//   · 웹뷰 안의 코드가 직접 못 읽는다 — IPC 를 거쳐야 하고, 그 표면은 우리가 정한다
// macOS 키체인만 다른 앱에 프롬프트를 띄운다.
//
// ⚠ 저장소가 **없는 기기가 있다**(데스크톱 세션 없는 리눅스, secret-service 데몬 부재).
// 그때 조용히 실패하면 사용자는 로그인이 안 되는 이유를 영영 모른다. 그래서 `available()`
// 을 따로 내주고, 프런트가 그 사실을 화면에 적은 뒤 이전 자리로 떨어진다.

use keyring::Entry;

/// 저장소 안에서의 이름공간. 번들 식별자와 맞춘다(다른 앱과 안 섞이게).
const SERVICE: &str = "io.vincs.app";

fn entry(name: &str) -> Result<Entry, String> {
    Entry::new(SERVICE, name).map_err(|e| e.to_string())
}

/// 값 읽기. **없는 것과 실패한 것을 가른다** — 없으면 `Ok(None)`, 저장소 자체가
/// 안 되면 `Err`. 이 둘을 뭉개면 프런트가 "로그인 안 됨"과 "읽을 수 없음"을 구분
/// 못 하고, 후자에서 사용자에게 다시 로그인하라고 말하게 된다(그래도 안 된다).
pub fn get(name: &str) -> Result<Option<String>, String> {
    match entry(name)?.get_password() {
        Ok(v) => Ok(Some(v)),
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(e) => Err(e.to_string()),
    }
}

pub fn set(name: &str, value: &str) -> Result<(), String> {
    entry(name)?.set_password(value).map_err(|e| e.to_string())
}

/// 지우기. 이미 없으면 성공으로 본다 — 로그아웃을 두 번 눌러도 오류가 나면 안 된다.
pub fn delete(name: &str) -> Result<(), String> {
    match entry(name)?.delete_credential() {
        Ok(()) => Ok(()),
        Err(keyring::Error::NoEntry) => Ok(()),
        Err(e) => Err(e.to_string()),
    }
}

/// 이 기기에 저장소가 실제로 있는가. **쓰고 지워 본다** — 열리는지만 보면 데몬이
/// 없는 리눅스에서 통과했다가 쓰기에서 죽는다.
pub fn available() -> bool {
    const PROBE: &str = "__vinc_probe__";
    if set(PROBE, "1").is_err() {
        return false;
    }
    let ok = matches!(get(PROBE), Ok(Some(_)));
    let _ = delete(PROBE);
    ok
}
