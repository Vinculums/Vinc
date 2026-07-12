# Changelog

Vinc 데스크톱 앱 릴리스 기록. 사용자 노출 카피 정본은 `site/release/`(웹 릴리스 노트)이며,
이 파일은 개발자용 요약이다.

## v0.1.1 — 2026-07-13

- **fix(sidecar)**: 첫 실행 데이터 적재 실패 해소. 신규 설치본에서 로컬 스토어 스키마가
  프로비저닝되지 않아 모든 적재(동봉 샘플 팩 포함)가 `Table Concept does not exist` 로
  실패하던 문제 — `VincApp.open` 이 기동 시 스키마를 멱등 생성한다. (`api/server.py`, 0d8106c)
- **fix(app)**: 패키징된 셸에서 프론트엔드 데이터 호출이 사이드카가 아닌 webview origin
  (`tauri://localhost` 등)으로 향하던 문제 — 셸 감지 시 기본 base 를 셸이 고정한
  `http://127.0.0.1:8787` 로 설정. Settings 의 수동 지정은 계속 우선한다. (`app/src/api.ts`)
- **build(installer)**: Windows NSIS 설치관리자 per-user 설치(관리자 권한 불요) + 설치 중
  한국어/영어 언어 선택. (`tauri.conf.json`, 26455b4)
- site: `/download` 첫 실행 우회 안내(미서명 빌드) · SignPath 귀속 문구, `/release` v0.1.0 노트.

## v0.1.0 — 2026-07-13

- 첫 공개 빌드 — 오프라인 코어(적재·그래프·의미 검색·팩) + Tauri 데스크톱 셸 + MCP 커넥터.
- 설치본: macOS .dmg(aarch64) · Windows .exe(NSIS) · Linux .AppImage/.deb.
- 알려진 문제: **신규 설치에서 데이터 적재 불가**(v0.1.1 에서 수정).
