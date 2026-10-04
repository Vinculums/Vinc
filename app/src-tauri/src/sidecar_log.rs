//! Relay engine output without treating stderr itself as a warning.

use log::Level;

fn explicit_level(message: &str) -> Option<Level> {
    let upper = message.to_ascii_uppercase();
    for (name, level) in [
        ("ERROR", Level::Error), ("CRITICAL", Level::Error),
        ("FATAL", Level::Error), ("WARNING", Level::Warn),
        ("WARN", Level::Warn), ("INFO", Level::Info),
        ("DEBUG", Level::Debug), ("TRACE", Level::Trace),
    ] {
        if upper.starts_with(&format!("[{name}]"))
            || upper.starts_with(&format!("{name}:"))
            || upper.starts_with(&format!("{name} "))
            || upper == name
        {
            return Some(level);
        }
    }
    message.starts_with('⚠').then_some(Level::Warn)
}

fn line_level(line: &str) -> Level {
    let line = line.trim();
    if let Some(level) = explicit_level(line) { return level; }
    let message = ["vinc:", "vinc-store:", "[embedded_backend]"]
        .into_iter()
        .find_map(|prefix| line.strip_prefix(prefix))
        .unwrap_or(line)
        .trim_start();
    explicit_level(message).unwrap_or(Level::Info)
}

pub fn output_level(output: &str) -> Level {
    // A transport event can contain several lines. Keep its strongest signal.
    output.lines().filter(|line| !line.trim().is_empty())
        .map(line_level).min().unwrap_or(Level::Info)
}

pub fn exit_level(code: Option<i32>, signal: Option<i32>) -> Level {
    if code == Some(0) && signal.is_none() { Level::Info } else { Level::Error }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn healthy_engine_output_is_info_on_either_stream() {
        for line in [
            "vinc: full-text index (Chunk) built at boot (0.2s)",
            "vinc: node mirror index (NodeVec) built at boot (0.0s)",
            "vinc: MENTIONS 재링크 — 규칙 (없음) → 3:title-before-source · {}",
            "vinc: gc tombstones — removed 0",
            r#"{"token":"<redacted>","error":null,"warning":false}"#,
        ] { assert_eq!(output_level(line), Level::Info, "{line}"); }
    }

    #[test]
    fn explicit_engine_levels_and_warning_markers_survive() {
        for line in ["WARNING: retry", "vinc: warn: retry", "[WARN] retry",
                     "vinc: ⚠ index heal failed", "[embedded_backend] ⚠ WAL repair"] {
            assert_eq!(output_level(line), Level::Warn, "{line}");
        }
        for line in ["ERROR: unavailable", "[ERROR] unavailable", "vinc: FATAL: unavailable"] {
            assert_eq!(output_level(line), Level::Error, "{line}");
        }
        assert_eq!(output_level("DEBUG: health poll"), Level::Debug);
        assert_eq!(output_level("normal output\nERROR: unavailable"), Level::Error);
        assert_eq!(output_level("document title: ERROR: a quoted word"), Level::Info);
    }

    #[test]
    fn only_a_successful_exit_is_info() {
        assert_eq!(exit_level(Some(0), None), Level::Info);
        assert_eq!(exit_level(Some(1), None), Level::Error);
        assert_eq!(exit_level(None, Some(9)), Level::Error);
        assert_eq!(exit_level(Some(0), Some(15)), Level::Error);
        assert_eq!(exit_level(None, None), Level::Error);
    }
}
