# Vinc-docs/DESIGN — Vinc 디자인 시스템 데이터

weg-9000-design 엔진의 **데이터 레포**(엔진=weg-9000-design, 데이터=여기).
revamp ↔ weg-9000-docs 와 동형.

- `vinc.profile.yml` — 엔진 프로파일 오버라이드(경로·팔레트·a11y)
- `UI_INVENTORY_AUDIT.md` — **P0 산출물**: 화면 5·GraphView 구조 골격 감사(카피 미승계)
- `design-tokens.json` · `tokens.css` · `DESIGN.md` — **생성 완료**(토큰 SSoT + 엔진 산출 CSS + 철학·규약 정본)

## 실행 (M4 이후)

```bash
# weg-9000-design 엔진에서:
python scripts/validate_design.py --tokens ../Vinc-docs/DESIGN/design-tokens.json \
    --profile ../Vinc-docs/DESIGN/vinc.profile.yml
python scripts/gen_tokens.py --check
```

> 토큰 산출물 생성 완료(`design-tokens.json`·`tokens.css`·`DESIGN.md`). 데스크톱(Tauri 웹뷰) 표면 전제.
