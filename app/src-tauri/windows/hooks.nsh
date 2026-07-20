; Vinc NSIS 설치/제거 훅 — 사이드카 파일 락 해제.
;
; 증상: 설치·자동업데이트 중 "다음 파일을 열 수 없습니다: …\vinc-sidecar.exe"
;       → '무시'해야 설치가 이어짐. 원인은 Defender 오탐이 아니라 **파일 락**:
;       실행 중인 PyInstaller onefile 사이드카(부트로더 + 파이썬 자식 = 프로세스 트리)가
;       vinc-sidecar.exe 이미지를 메모리 매핑해 붙잡고 있어 설치기가 덮어쓰지 못한다.
;
; 대응: 파일을 설치(덮어쓰기)하기 직전에 잔존 사이드카 트리를 강제 종료해 락을 푼다.
;       /T = 자식 프로세스까지(부트로더의 파이썬 자식), /F = 강제. 미실행/부재 시
;       taskkill 반환코드는 무시(멱등). nsExec::Exec 는 창 없이 조용히 실행.
;
; NOTE: /IM(이미지명)은 Settings→MCP 로 Claude 클라이언트에 붙은 브리지
;       (`vinc-sidecar.exe mcp serve`)까지 종료한다. 설치 시엔 그 브리지도 같은 .exe
;       이미지를 물고 있어 덮어쓰려면 반드시 함께 정리해야 하므로 의도된·필수 동작이다
;       (Claude 는 stdio 서버를 자동 재기동, 설치 후 새 바이너리로 재연결). 반면 앱
;       런타임 시작 경로에서는 이 부수효과를 피하려 이미지명 회수를 하지 않는다(main.rs).
;
; 앱 코드에도 다층 방어가 있다(single-instance·종료 시 트리-kill·--parent-pid heartbeat)
; — 이 훅은 그중 인스톨러 경로(수동 재설치·업데이터)와 구버전 잔존 고아 정리를 덮는다.

!macro NSIS_HOOK_PREINSTALL
  DetailPrint "Stopping Vinc sidecar to release file lock..."
  nsExec::Exec 'taskkill /F /T /IM vinc-sidecar.exe'
  Pop $0
  Sleep 300   ; OS 가 파일 핸들을 실제로 놓을 여유
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  DetailPrint "Stopping Vinc sidecar..."
  nsExec::Exec 'taskkill /F /T /IM vinc-sidecar.exe'
  Pop $0
  Sleep 300
!macroend
