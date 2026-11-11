// 저장 위치가 사용자 데이터베이스일 때 그 비밀번호를 사이드카에 넘긴다.
//
// 계약: `~/.vinc/config.json` 이 `mode: "database"` 이고 `database.secret_ref` 가 키링 항목의 이름이면,
// 셸은 사이드카를 띄울 때 그 값을 키링(`secret.rs`, 계정 키와 같은 저장소)에서 꺼내 자식 프로세스의
// 환경 `VINC_DATABASE_SECRET` 으로만 싣는다. 명령행·설정 파일·로그에는 싣지 않는다. 사이드카는 읽자마자
// 환경에서 지운다(`store/location.py` 의 `take_secret`).
//
// 값이 없거나 키링을 못 읽으면 넘기지 않는다. 그러면 사이드카가 스토어 없이 뜨고 `/health` 에 사유를
// 싣는다 — 폴더 스토어로 물러서지 않는다.

/// 사이드카가 비밀번호를 받는 환경 변수(`store/location.py` 의 `SECRET_ENV` 와 같다).
pub const SECRET_ENV: &str = "VINC_DATABASE_SECRET";

/// 설정 본문에서 키링 항목 이름을 고른다. 데이터베이스 위치가 아니거나 이름 모양이 틀리면 None.
/// 이름 규칙은 `store/location.py` 의 `_SECRET_REF` 와 같다(영숫자와 `._:/@-`, 1~200자).
pub fn secret_ref(config: &str) -> Option<String> {
    let v: serde_json::Value = serde_json::from_str(config).ok()?;
    if v.get("mode")?.as_str()? != "database" {
        return None;
    }
    let name = v.get("database")?.get("secret_ref")?.as_str()?;
    let ok = !name.is_empty()
        && name.len() <= 200
        && name
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || "._:/@-".contains(c));
    if ok {
        Some(name.to_string())
    } else {
        None
    }
}

/// `home` 아래 설정을 읽어 비밀번호를 꺼낸다. 데이터베이스 위치가 아니면 조용히 None.
pub fn database_secret(home: &std::path::Path) -> Option<String> {
    let text = std::fs::read_to_string(home.join(".vinc").join("config.json")).ok()?;
    let name = secret_ref(&text)?;
    match crate::secret::get(&name) {
        Ok(Some(value)) => Some(value),
        Ok(None) => {
            log::warn!(target: "sidecar", "database password not in the keyring ({name})");
            None
        }
        Err(e) => {
            log::warn!(target: "sidecar", "keyring read failed for the database password: {e}");
            None
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn 데이터베이스_위치의_이름만_고른다() {
        let db = r#"{"mode":"database","database":{"secret_ref":"vinc-database:graph.example.com/vinc"}}"#;
        assert_eq!(secret_ref(db).as_deref(), Some("vinc-database:graph.example.com/vinc"));
        assert_eq!(secret_ref(r#"{"mode":"local","database":{"secret_ref":"x"}}"#), None);
        assert_eq!(secret_ref(r#"{"mode":"database"}"#), None);
        assert_eq!(secret_ref(r#"{"mode":"database","database":{"secret_ref":"has space"}}"#), None);
        assert_eq!(secret_ref("not json"), None);
    }
}
