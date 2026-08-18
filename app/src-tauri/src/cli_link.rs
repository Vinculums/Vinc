// 터미널에서 `vinc` 를 부를 수 있게 하는 링크 (B 2단계).
//
// 배경: 이 바이너리는 CLI 동사를 받으면 창을 열지 않고 엔진으로 넘긴다(main.rs 의
// `cli_forward`). 그런데 그 이름을 **부를 수 있는 자리**는 채널마다 다르다.
//
//   deb·rpm   패키지가 `/usr/bin/vinc` 로 깔아 준다 → 여기서 할 일 없음
//   macOS     드래그 설치라 훅이 없다 → 앱이 실행 중에 링크를 건다(이 모듈)
//   AppImage  설치 단계가 없다 → 마찬가지로 이 모듈. `$APPIMAGE` 를 가리킨다
//   Windows   설치기가 PATH 를 건드려야 한다 → 이 모듈 밖(다음 단계)
//
// ⚠ 권한 상승을 하지 않는다. `/usr/local/bin` 이 쓸 수 있으면 거기, 아니면
// `~/.local/bin` 이다. 관리자 암호를 묻는 편이 UX 는 나아 보이지만, 그 프롬프트는
// "앱이 왜 내 암호를 묻지"라는 질문을 만들고 거절당하면 남는 게 없다. 대신 어디에
// 걸었는지와 PATH 에 없을 때 붙일 한 줄을 그대로 돌려준다.
//
// ⚠ 남의 `vinc` 를 건드리지 않는다. 목적지가 이미 있는데 우리 것이 아니면 실패로
// 알린다. 지울 때도 **우리가 건 심링크일 때만** 지운다 — 실파일은 절대 안 지운다.

// ⚠ 이 모듈은 **모든 플랫폼에서 컴파일된다.** unix 전용 구현만 `#[cfg(unix)]` 로 가른다.
// 파일 통째로 `#![cfg(unix)]` 을 걸면 Windows 에서 `Status` 자체가 사라져, 그것을
// 참조하는 커맨드 시그니처가 깨진다(이 리포는 CI 에서 러스트를 빌드하지 않으므로
// 그런 실수는 태그를 붙일 때에야 드러난다).

#[cfg(unix)]
use std::path::{Path, PathBuf};

/// 프론트가 받는 상태 — **사실만** 담는다. 보여 줄 문장은 프론트가 짓는다.
#[derive(Clone, serde::Serialize)]
pub struct Status {
    /// 이 플랫폼에서 이 동작이 의미가 있는가(Windows 는 false).
    pub supported: bool,
    /// 우리 링크가 걸려 있는가.
    pub linked: bool,
    /// 걸려 있다면 그 경로(없으면 빈 문자열).
    pub path: String,
    /// 링크가 가리키는(가리킬) 실행 파일.
    pub target: String,
    /// 링크를 건(걸) 디렉터리.
    pub dir: String,
    /// 그 자리가 셸 PATH 에 있을 것으로 기대되는가.
    /// ⚠ 사실만 준다 — 안내 문장은 프론트가 사용자 언어로 짓는다. 러스트가 산문을
    /// 돌려주면 영어 사용자에게 한국어가 그대로 나간다.
    pub on_path: bool,
}

/// 링크가 가리켜야 할 실행 파일.
///
/// AppImage 안에서 `current_exe()` 는 실행 중에만 존재하는 마운트 지점
/// (`/tmp/.mount_…/usr/bin/vinc`)을 가리킨다. 그 경로로 링크를 걸면 앱을 닫는
/// 순간 끊긴 링크가 된다. AppImage 런타임이 실제 `.AppImage` 파일 경로를
/// `$APPIMAGE` 로 넘겨 주므로 그쪽을 우선한다.
#[cfg(unix)]
pub fn source_from(appimage: Option<String>, current_exe: PathBuf) -> PathBuf {
    match appimage {
        Some(p) if !p.trim().is_empty() => PathBuf::from(p),
        _ => current_exe,
    }
}

#[cfg(unix)]
fn source() -> Option<PathBuf> {
    Some(source_from(
        std::env::var("APPIMAGE").ok(),
        std::env::current_exe().ok()?,
    ))
}

/// 링크를 걸 후보 — 앞엣것부터 쓴다.
/// `VINC_CLI_LINK_DIR` 가 있으면 그 자리를 가장 먼저 본다. 표준 자리가 둘 다 막힌
/// 환경(회사 관리 기기 등)에서 빠져나갈 구멍이자, 이 모듈의 설치·해제 경로를 실제로
/// 돌려 보는 테스트의 통로다.
#[cfg(unix)]
fn candidates() -> Vec<PathBuf> {
    let mut v = Vec::new();
    if let Some(d) = std::env::var_os("VINC_CLI_LINK_DIR") {
        if !d.is_empty() {
            v.push(PathBuf::from(d));
        }
    }
    v.push(PathBuf::from("/usr/local/bin"));
    if let Some(home) = std::env::var_os("HOME") {
        v.push(PathBuf::from(home).join(".local/bin"));
    }
    v
}

/// 디렉터리에 실제로 쓸 수 있는지 **시도해서** 본다. 퍼미션 비트만 보면
/// 그룹·ACL·읽기전용 마운트에서 틀린 답이 나온다.
#[cfg(unix)]
fn writable(dir: &Path) -> bool {
    if !dir.is_dir() {
        return false;
    }
    let probe = dir.join(".vinc-write-probe");
    match std::fs::write(&probe, b"") {
        Ok(()) => {
            let _ = std::fs::remove_file(&probe);
            true
        }
        Err(_) => false,
    }
}

/// 후보 중 쓸 수 있는 첫 자리. 없으면 `~/.local/bin` 을 **만들어서** 쓴다.
#[cfg(unix)]
fn choose_dir() -> Option<PathBuf> {
    if let Some(d) = candidates().into_iter().find(|d| writable(d)) {
        return Some(d);
    }
    let home = std::env::var_os("HOME")?;
    let local = PathBuf::from(home).join(".local/bin");
    std::fs::create_dir_all(&local).ok()?;
    writable(&local).then_some(local)
}

/// PATH 에 있을 것으로 기대되는 자리인가. ⚠ 앱 프로세스의 `$PATH` 로 판정하지
/// **않는다** — GUI 로 띄운 앱은 셸 PATH 를 물려받지 않아서, 터미널에서는 잡히는
/// 자리도 여기서는 없어 보인다. 그래서 관례로만 판단한다.
#[cfg(unix)]
fn likely_on_path(dir: &Path) -> bool {
    dir == Path::new("/usr/local/bin") || dir == Path::new("/usr/bin")
}

/// 이 경로가 **우리가 건 심링크**인가. 우리 것이 아니면 건드리지 않기 위한 판정.
#[cfg(unix)]
fn is_our_link(link: &Path, src: &Path) -> bool {
    std::fs::read_link(link).map(|t| t == src).unwrap_or(false)
}

#[cfg(unix)]
fn find_existing(src: &Path) -> Option<PathBuf> {
    candidates()
        .into_iter()
        .map(|d| d.join("vinc"))
        .find(|p| is_our_link(p, src))
}

#[cfg(unix)]
pub fn status() -> Status {
    let Some(src) = source() else {
        return Status {
            supported: false,
            linked: false,
            path: String::new(),
            target: String::new(),
            dir: String::new(),
            on_path: false,
        };
    };
    match find_existing(&src) {
        Some(p) => {
            let dir = p.parent().map(Path::to_path_buf).unwrap_or_default();
            Status {
                supported: true,
                linked: true,
                path: p.display().to_string(),
                target: src.display().to_string(),
                on_path: likely_on_path(&dir),
                dir: dir.display().to_string(),
            }
        }
        None => Status {
            supported: true,
            linked: false,
            path: String::new(),
            target: src.display().to_string(),
            dir: String::new(),
            on_path: false,
        },
    }
}

#[cfg(unix)]
pub fn install() -> Result<Status, String> {
    let src = source().ok_or_else(|| "cannot resolve this executable's path".to_string())?;
    if let Some(p) = find_existing(&src) {
        let dir = p.parent().map(Path::to_path_buf).unwrap_or_default();
        return Ok(Status {
            supported: true,
            linked: true,
            path: p.display().to_string(),
            target: src.display().to_string(),
            on_path: likely_on_path(&dir),
            dir: dir.display().to_string(),
        });
    }
    let dir = choose_dir().ok_or_else(|| {
        "no writable directory (/usr/local/bin, ~/.local/bin)".to_string()
    })?;
    let link = dir.join("vinc");
    if link.exists() || link.symlink_metadata().is_ok() {
        // 이미 뭔가 있다. 우리 것이 아니므로 덮지 않는다.
        return Err(format!(
            "{} already exists and is not ours — remove it first",
            link.display()
        ));
    }
    std::os::unix::fs::symlink(&src, &link)
        .map_err(|e| format!("cannot link {}: {e}", link.display()))?;
    Ok(Status {
        supported: true,
        linked: true,
        path: link.display().to_string(),
        target: src.display().to_string(),
        on_path: likely_on_path(&dir),
        dir: dir.display().to_string(),
    })
}

#[cfg(unix)]
pub fn remove() -> Result<Status, String> {
    let src = source().ok_or_else(|| "cannot resolve this executable's path".to_string())?;
    if let Some(p) = find_existing(&src) {
        std::fs::remove_file(&p).map_err(|e| format!("cannot remove {}: {e}", p.display()))?;
    }
    Ok(Status {
        supported: true,
        linked: false,
        path: String::new(),
        target: src.display().to_string(),
        dir: String::new(),
        on_path: false,
    })
}

/// Windows 에서는 설치기가 PATH 를 다룬다(다음 단계). 화면은 이 상태를 받아
/// 해당 행을 숨기거나 "설치기가 처리함"으로 표시한다.
#[cfg(not(unix))]
pub fn unsupported() -> Status {
    Status {
        supported: false,
        linked: false,
        path: String::new(),
        target: String::new(),
        dir: String::new(),
        on_path: false,
    }
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;

    /// AppImage 안의 `current_exe()` 는 실행 중에만 있는 마운트 지점이다. 그 경로로
    /// 링크를 걸면 앱을 닫는 순간 끊긴다 — `$APPIMAGE` 가 있으면 그쪽이어야 한다.
    #[test]
    fn appimage_가_있으면_그_경로를_가리킨다() {
        let mount = PathBuf::from("/tmp/.mount_vinc12/usr/bin/vinc");
        assert_eq!(
            source_from(Some("/home/u/Apps/Vinc.AppImage".into()), mount.clone()),
            PathBuf::from("/home/u/Apps/Vinc.AppImage")
        );
        assert_eq!(source_from(None, mount.clone()), mount);
        // 빈 값은 없는 것과 같게 다룬다(런타임이 빈 문자열을 넘기는 경우).
        assert_eq!(source_from(Some("".into()), mount.clone()), mount);
        assert_eq!(source_from(Some("   ".into()), mount.clone()), mount);
    }

    #[test]
    fn 셸_path_판정은_관례로만_한다() {
        assert!(likely_on_path(Path::new("/usr/local/bin")));
        assert!(likely_on_path(Path::new("/usr/bin")));
        assert!(!likely_on_path(Path::new("/home/u/.local/bin")));
    }

    /// 남의 `vinc` 를 지우지 않기 위한 판정 — 우리 소스를 가리키는 심링크만 우리 것이다.
    #[test]
    fn 우리_링크만_우리_것으로_본다() {
        let dir = std::env::temp_dir().join("vinc-link-test");
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let src = dir.join("Vinc.AppImage");
        let other = dir.join("someone-elses");
        std::fs::write(&src, b"x").unwrap();
        std::fs::write(&other, b"x").unwrap();

        let ours = dir.join("vinc-ours");
        let theirs = dir.join("vinc-theirs");
        std::os::unix::fs::symlink(&src, &ours).unwrap();
        std::os::unix::fs::symlink(&other, &theirs).unwrap();

        assert!(is_our_link(&ours, &src));
        assert!(!is_our_link(&theirs, &src), "다른 곳을 가리키면 우리 것이 아니다");
        assert!(!is_our_link(&src, &src), "실파일은 심링크가 아니다 — 지우면 안 된다");
        assert!(!is_our_link(&dir.join("없음"), &src));
    }

    /// 설치·해제 경로를 **실제로** 돌린다. `VINC_CLI_LINK_DIR` 로 임시 자리를 지정해
    /// 이 기기의 `/usr/local/bin` 을 건드리지 않는다.
    /// ⚠ 환경변수는 프로세스 전역이라 이 테스트 하나가 전 과정을 순서대로 밟는다.
    #[test]
    fn 설치와_해제가_실제로_돈다() {
        let dir = std::env::temp_dir().join("vinc-cli-link-e2e");
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        // SAFETY: 이 테스트는 env 를 만지는 유일한 테스트다(다른 테스트는 순수 함수).
        unsafe { std::env::set_var("VINC_CLI_LINK_DIR", &dir) };

        let before = status();
        assert!(before.supported && !before.linked, "처음엔 링크가 없다");

        let after = install().expect("설치되어야 한다");
        let link = dir.join("vinc");
        assert!(after.linked && after.path == link.display().to_string());
        assert!(link.symlink_metadata().unwrap().file_type().is_symlink());
        assert_eq!(std::fs::read_link(&link).unwrap(), std::env::current_exe().unwrap());
        assert!(!after.on_path, "임시 자리는 PATH 로 치지 않는다");

        // 멱등: 이미 걸려 있으면 다시 걸지 않고 같은 상태를 돌려준다.
        let again = install().expect("이미 걸려 있어도 성공");
        assert_eq!(again.path, after.path);

        let gone = remove().expect("해제되어야 한다");
        assert!(!gone.linked);
        assert!(link.symlink_metadata().is_err(), "링크가 지워져야 한다");

        // 남의 `vinc` 는 덮지도 지우지도 않는다.
        std::fs::write(&link, b"someone else's binary").unwrap();
        assert!(install().is_err(), "우리 것이 아니면 실패해야 한다");
        remove().expect("해제는 조용히 성공하되");
        assert!(link.is_file(), "남의 실파일은 그대로 남아야 한다");

        unsafe { std::env::remove_var("VINC_CLI_LINK_DIR") };
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn 쓸_수_있는지는_시도해서_본다() {
        let dir = std::env::temp_dir().join("vinc-writable-test");
        let _ = std::fs::create_dir_all(&dir);
        assert!(writable(&dir));
        assert!(!writable(&dir.join("존재하지-않는-하위")));
        assert!(!writable(Path::new("/proc/self/ns")), "쓸 수 없는 자리는 false");
    }
}
