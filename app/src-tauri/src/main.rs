// Vinc 데스크톱 셸 진입점. 실제 로직은 lib.rs(데스크톱/모바일 공유).
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    vinc_app_lib::run()
}
