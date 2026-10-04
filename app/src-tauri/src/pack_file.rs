use std::io::Write;
use tauri_plugin_dialog::DialogExt;

/// 파일 아티팩트만 저장한다. 그래프 읽기·쓰기는 기존 HTTP 경로의 몫이다.
/// 호출자가 경로를 넘기지 못하게 하고, 사람이 저장 창에서 고른 경로만 쓴다.
#[tauri::command]
pub async fn save_pack_file(
    app: tauri::AppHandle,
    filename: String,
    title: String,
    bytes: Vec<u8>,
) -> Result<Option<String>, String> {
    if !bytes.starts_with(b"PK") {
        return Err("invalid pack container".into());
    }
    let name = std::path::Path::new(&filename)
        .file_name().ok_or("invalid filename")?.to_string_lossy().into_owned();
    tauri::async_runtime::spawn_blocking(move || {
        let Some(selected) = app.dialog().file().set_title(title).set_file_name(name)
            .add_filter("Vinc", &["vincpack"]).blocking_save_file() else {
            return Ok(None);
        };
        let path = selected.into_path().map_err(|e| e.to_string())?;
        if !path.is_absolute() {
            return Err("save dialog must return an absolute path".into());
        }
        if path.extension().and_then(|e| e.to_str()) != Some("vincpack") {
            return Err("pack filename must end in .vincpack".into());
        }
        // 같은 파일을 다시 고르거나 경합해도 기존 파일을 조용히 덮지 않는다.
        let mut file = std::fs::OpenOptions::new().write(true).create_new(true)
            .open(&path).map_err(|e| e.to_string())?;
        file.write_all(&bytes).map_err(|e| e.to_string())?;
        file.sync_all().map_err(|e| e.to_string())?;
        Ok(Some(path.to_string_lossy().into_owned()))
    }).await.map_err(|e| e.to_string())?
}
