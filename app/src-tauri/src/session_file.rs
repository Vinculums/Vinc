//! 연결 파일은 비밀값을 쓰기 전에 폴더와 임시 파일의 접근 권한을 제한한다.
use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};

static NEXT_TEMP: AtomicU64 = AtomicU64::new(0);

pub fn write_private(path: &Path, body: &[u8]) -> io::Result<()> {
    let dir = path
        .parent()
        .ok_or_else(|| io::Error::other("missing session directory"))?;
    fs::create_dir_all(dir)?;
    // 링크를 따라 다른 폴더의 권한을 바꾸지 않는다.
    if !fs::symlink_metadata(dir)?.is_dir() {
        return Err(io::Error::other(
            "session directory must be a real directory",
        ));
    }
    restrict(dir, true)?;
    let tmp = dir.join(format!(
        ".session-{}-{}.tmp",
        std::process::id(),
        NEXT_TEMP.fetch_add(1, Ordering::Relaxed)
    ));
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    // create_new 는 기존 임시 파일이나 심볼릭 링크를 열지 않는다.
    let mut file = options.open(&tmp)?;
    let result = (|| {
        restrict(&tmp, false)?;
        file.write_all(body)?;
        file.sync_all()?;
        drop(file);
        fs::rename(&tmp, path)
    })();
    if result.is_err() {
        let _ = fs::remove_file(&tmp);
    }
    result
}

#[cfg(unix)]
fn restrict(path: &Path, directory: bool) -> io::Result<()> {
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(
        path,
        fs::Permissions::from_mode(if directory { 0o700 } else { 0o600 }),
    )
}

#[cfg(windows)]
mod windows_acl {
    use super::*;
    use std::os::windows::ffi::OsStrExt;
    use std::ptr::{null, null_mut};
    use windows_sys::Win32::Foundation::{CloseHandle, LocalFree, HANDLE};
    use windows_sys::Win32::Security::Authorization::{
        ConvertSidToStringSidW, ConvertStringSecurityDescriptorToSecurityDescriptorW,
        SetNamedSecurityInfoW, SDDL_REVISION_1, SE_FILE_OBJECT,
    };
    use windows_sys::Win32::Security::{
        GetSecurityDescriptorDacl, GetTokenInformation, TokenUser, DACL_SECURITY_INFORMATION,
        PROTECTED_DACL_SECURITY_INFORMATION, TOKEN_QUERY, TOKEN_USER,
    };
    use windows_sys::Win32::System::Threading::{GetCurrentProcess, OpenProcessToken};

    // Windows 가 할당한 SID 문자열·보안 설명자는 LocalFree 로 돌려준다.
    struct LocalMemory(*mut std::ffi::c_void);
    impl Drop for LocalMemory {
        fn drop(&mut self) {
            unsafe {
                LocalFree(self.0);
            }
        }
    }
    struct TokenHandle(HANDLE);
    impl Drop for TokenHandle {
        fn drop(&mut self) {
            unsafe {
                CloseHandle(self.0);
            }
        }
    }

    pub fn user_sid() -> io::Result<String> {
        unsafe {
            let mut token = null_mut();
            if OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut token) == 0 {
                return Err(io::Error::last_os_error());
            }
            let _handle = TokenHandle(token);
            let mut size = 0;
            GetTokenInformation(token, TokenUser, null_mut(), 0, &mut size);
            if size == 0 {
                return Err(io::Error::last_os_error());
            }
            // TOKEN_USER 를 읽을 수 있는 정렬을 유지한다.
            let mut data = vec![0usize; (size as usize).div_ceil(std::mem::size_of::<usize>())];
            if GetTokenInformation(token, TokenUser, data.as_mut_ptr().cast(), size, &mut size) == 0
            {
                return Err(io::Error::last_os_error());
            }
            let user = &*data.as_ptr().cast::<TOKEN_USER>();
            let mut sid = null_mut();
            if ConvertSidToStringSidW(user.User.Sid, &mut sid) == 0 {
                return Err(io::Error::last_os_error());
            }
            let _allocation = LocalMemory(sid.cast());
            let mut len = 0;
            while *sid.add(len) != 0 {
                len += 1;
            }
            Ok(String::from_utf16_lossy(std::slice::from_raw_parts(
                sid, len,
            )))
        }
    }

    pub fn restrict(path: &Path, directory: bool) -> io::Result<()> {
        let sid = user_sid()?;
        // 상속은 끄고 이 사용자와 SYSTEM 만 허용한다. 새 자식도 같은 제한을 물려받는다.
        let flags = if directory { "OICI" } else { "" };
        let sddl: Vec<u16> = format!("D:P(A;{flags};FA;;;SY)(A;{flags};FA;;;{sid})")
            .encode_utf16()
            .chain(Some(0))
            .collect();
        let name: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
        unsafe {
            let mut descriptor = null_mut();
            if ConvertStringSecurityDescriptorToSecurityDescriptorW(
                sddl.as_ptr(),
                SDDL_REVISION_1,
                &mut descriptor,
                null_mut(),
            ) == 0
            {
                return Err(io::Error::last_os_error());
            }
            let _allocation = LocalMemory(descriptor);
            let (mut present, mut defaulted, mut acl) = (0, 0, null_mut());
            if GetSecurityDescriptorDacl(descriptor, &mut present, &mut acl, &mut defaulted) == 0 {
                return Err(io::Error::last_os_error());
            }
            if present == 0 || acl.is_null() {
                return Err(io::Error::other("session DACL is missing"));
            }
            let error = SetNamedSecurityInfoW(
                name.as_ptr(),
                SE_FILE_OBJECT,
                DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
                null_mut(),
                null_mut(),
                acl,
                null(),
            );
            if error == 0 {
                Ok(())
            } else {
                Err(io::Error::from_raw_os_error(error as i32))
            }
        }
    }
}

#[cfg(windows)]
use windows_acl::restrict;

#[cfg(test)]
mod tests {
    use super::*;

    struct Temp(std::path::PathBuf);
    impl Temp {
        fn new() -> Self {
            let path = std::env::temp_dir().join(format!(
                "vinc-session-test-{}-{}",
                std::process::id(),
                NEXT_TEMP.fetch_add(1, Ordering::Relaxed)
            ));
            fs::create_dir(&path).unwrap();
            Self(path)
        }
    }
    impl Drop for Temp {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn replace_session_preserves_contents_and_leaves_no_temp_file() {
        let temp = Temp::new();
        let path = temp.0.join(".vinc/session.json");
        write_private(&path, b"first").unwrap();
        write_private(&path, b"second").unwrap();
        assert_eq!(fs::read(&path).unwrap(), b"second");
        assert_eq!(fs::read_dir(path.parent().unwrap()).unwrap().count(), 1);
    }

    #[test]
    fn invalid_directory_fails_without_writing() {
        let temp = Temp::new();
        let file = temp.0.join(".vinc");
        fs::write(&file, b"existing").unwrap();
        assert!(write_private(&file.join("session.json"), b"secret").is_err());
        assert_eq!(fs::read(file).unwrap(), b"existing");
    }

    #[cfg(unix)]
    #[test]
    fn repairs_permissive_directory_and_creates_private_file() {
        use std::os::unix::fs::PermissionsExt;
        let temp = Temp::new();
        let dir = temp.0.join(".vinc");
        fs::create_dir(&dir).unwrap();
        fs::set_permissions(&dir, fs::Permissions::from_mode(0o755)).unwrap();
        let path = dir.join("session.json");
        write_private(&path, b"secret").unwrap();
        assert_eq!(
            fs::metadata(&dir).unwrap().permissions().mode() & 0o777,
            0o700
        );
        assert_eq!(
            fs::metadata(path).unwrap().permissions().mode() & 0o777,
            0o600
        );
    }

    #[cfg(unix)]
    #[test]
    fn refuses_directory_symlink_without_changing_target() {
        use std::os::unix::fs::{symlink, PermissionsExt};
        let temp = Temp::new();
        let target = temp.0.join("target");
        fs::create_dir(&target).unwrap();
        fs::set_permissions(&target, fs::Permissions::from_mode(0o755)).unwrap();
        let link = temp.0.join(".vinc");
        symlink(&target, &link).unwrap();
        assert!(write_private(&link.join("session.json"), b"secret").is_err());
        assert_eq!(
            fs::metadata(target).unwrap().permissions().mode() & 0o777,
            0o755
        );
    }

    #[cfg(windows)]
    #[test]
    fn directory_and_file_have_protected_user_and_system_dacls() {
        use std::os::windows::ffi::OsStrExt;
        use std::ptr::null_mut;
        use windows_sys::Win32::Foundation::LocalFree;
        use windows_sys::Win32::Security::Authorization::{
            ConvertSecurityDescriptorToStringSecurityDescriptorW,
            ConvertStringSecurityDescriptorToSecurityDescriptorW, GetNamedSecurityInfoW,
            SDDL_REVISION_1, SE_FILE_OBJECT,
        };
        use windows_sys::Win32::Security::DACL_SECURITY_INFORMATION;
        unsafe fn descriptor_text(
            descriptor: windows_sys::Win32::Security::PSECURITY_DESCRIPTOR,
        ) -> String {
            let mut text = null_mut();
            assert_ne!(
                ConvertSecurityDescriptorToStringSecurityDescriptorW(
                    descriptor,
                    SDDL_REVISION_1,
                    DACL_SECURITY_INFORMATION,
                    &mut text,
                    null_mut()
                ),
                0
            );
            let mut len = 0;
            while *text.add(len) != 0 {
                len += 1;
            }
            let value = String::from_utf16_lossy(std::slice::from_raw_parts(text, len));
            LocalFree(text.cast());
            value
        }
        let temp = Temp::new();
        let path = temp.0.join(".vinc/session.json");
        write_private(&path, b"secret").unwrap();
        let sid = windows_acl::user_sid().unwrap();
        for (p, flags) in [(path.parent().unwrap(), "OICI"), (path.as_path(), "")] {
            let name: Vec<u16> = p.as_os_str().encode_wide().chain(Some(0)).collect();
            unsafe {
                let mut descriptor = null_mut();
                assert_eq!(
                    GetNamedSecurityInfoW(
                        name.as_ptr(),
                        SE_FILE_OBJECT,
                        DACL_SECURITY_INFORMATION,
                        null_mut(),
                        null_mut(),
                        null_mut(),
                        null_mut(),
                        &mut descriptor
                    ),
                    0
                );
                let actual = descriptor_text(descriptor);
                LocalFree(descriptor);
                // SID 의 숫자 표기와 계정 약칭을 같은 Windows 직렬화로 비교한다.
                let wanted: Vec<u16> = format!("D:P(A;{flags};FA;;;SY)(A;{flags};FA;;;{sid})")
                    .encode_utf16()
                    .chain(Some(0))
                    .collect();
                let mut expected_descriptor = null_mut();
                assert_ne!(
                    ConvertStringSecurityDescriptorToSecurityDescriptorW(
                        wanted.as_ptr(),
                        SDDL_REVISION_1,
                        &mut expected_descriptor,
                        null_mut()
                    ),
                    0
                );
                let expected = descriptor_text(expected_descriptor);
                LocalFree(expected_descriptor);
                let (control, aces) = actual.split_once('(').unwrap();
                // Windows 는 보호된 DACL 에도 자동 상속 처리 완료(AI) 표시를 남길 수 있다.
                assert!(control == "D:P" || control == "D:PAI", "{control}");
                assert_eq!(aces, expected.split_once('(').unwrap().1);
            }
        }
    }
}
