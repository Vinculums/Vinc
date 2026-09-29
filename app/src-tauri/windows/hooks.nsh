; Vinc NSIS 설치/제거 훅 — 엔진(사이드카) 파일 락 해제.
;
; 증상: 설치·자동업데이트 중 "다음 파일을 열 수 없습니다: …\vinc.exe"(옛 이름
;       vinc-sidecar.exe) → '무시'해야 설치가 이어짐. 원인은 Defender 오탐이 아니라
;       **파일 락**: 실행 중인 PyInstaller onefile 엔진(부트로더 + 파이썬 자식 = 프로세스
;       트리)이 exe 이미지를 메모리 매핑해 붙잡고 있어 설치기가 덮어쓰지 못한다.
;
; 대응: 파일을 설치(덮어쓰기)하기 직전에 잔존 엔진 트리를 강제 종료해 락을 푼다.
;       /T = 자식 프로세스까지(부트로더의 파이썬 자식), /F = 강제. 미실행/부재 시
;       taskkill 반환코드는 무시(멱등). nsExec::Exec 는 창 없이 조용히 실행.
;
; 이미지 이름 둘을 모두 끈다:
;   - vinc.exe          2026-09-18(VIN-72) 부터 엔진 이름. tauri.conf.json 의 externalBin
;                       `binaries/vinc` 가 설치 폴더에 vinc.exe 로 떨어진다.
;   - vinc-sidecar.exe  그 전 이름. 업데이트 전 설치본이 아직 이 이름으로 돌고 있을 수
;                       있고(main.rs `sidecar_binary()` 도 옛 이름을 찾는다), 그 프로세스가
;                       남으면 설치 폴더를 정리하지 못한다.
; ⚠ /IM 은 와일드카드(*) 없이 **이미지 이름 전체**를 비교한다. 그래서 `vinc.exe` 는 앱
;   바이너리 `vinc-app.exe`(mainBinaryName)를 끄지 않는다 — 설치기를 띄운 앱 자신은
;   Tauri 업데이터가 따로 닫는다. 여기에 `vinc*.exe` 같은 패턴을 쓰지 말 것.
;
; NOTE: /IM(이미지명)은 MCP 호스트에 붙은 stdio 브리지(`vinc.exe mcp serve`)까지
;       종료한다. 설치 시엔 그 브리지도 같은 .exe 이미지를 물고 있어 덮어쓰려면 반드시
;       함께 정리해야 하므로 의도된·필수 동작이다(호스트는 stdio 서버를 다시 띄우고, 설치
;       후 새 바이너리로 재연결). 반면 앱 런타임 시작 경로에서는 이 부수효과를 피하려
;       이미지명 회수를 하지 않는다(main.rs).
;
; 앱 코드에도 다층 방어가 있다(single-instance·종료 시 트리-kill·--parent-pid heartbeat)
; — 이 훅은 그중 인스톨러 경로(수동 재설치·업데이터)와 구버전 잔존 고아 정리를 덮는다.

!macro VINC_STOP_ENGINE
  nsExec::Exec 'taskkill /F /T /IM vinc.exe'
  Pop $0
  nsExec::Exec 'taskkill /F /T /IM vinc-sidecar.exe'
  Pop $0
  Sleep 300   ; OS 가 파일 핸들을 실제로 놓을 여유
!macroend

!macro NSIS_HOOK_PREINSTALL
  DetailPrint "Stopping the Vinc engine to release its file lock..."
  !insertmacro VINC_STOP_ENGINE
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  DetailPrint "Stopping the Vinc engine..."
  !insertmacro VINC_STOP_ENGINE
!macroend
