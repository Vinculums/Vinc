# Vinc 공식 홈페이지 (P5 선행 C)

정적 전용(remote-profile §5) — 빌드 단계 없음. 이 폴더가 곧 배포 산출물이다.

- 자기완결: 외부 폰트·스크립트·이미지·API 호출 0. 파일 1개(index.html).
- 색·형태는 Vinc-docs/DESIGN design-tokens.json 정본에서 복사(수기 동기 —
  토큰 개정 시 여기의 `:root`/`[data-theme="dark"]` 블록을 갱신).
- 카피는 앱과 동일 원칙: 기본 다크(D38), en/ko 로케일 추종(D42),
  em-dash 미사용(D38 구두점 규칙).
- 다운로드 버튼은 GitHub Releases latest 로 연결(P4 릴리스 파이프라인 산출물).

배포(서버 트랙 5단계): `rsync -av site/ user@host:/srv/www/` 후 Caddy 가 서빙
(deploy/Caddyfile 의 루트 도메인 블록).
