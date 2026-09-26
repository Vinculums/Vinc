# -*- coding: utf-8 -*-
"""figma_diff.py — Figma 라이브 덤프를 토큰 SSoT 와 대조한다. 정본은 **코드**다.

여태 Figma 와 코드가 맞는지는 **한 번도 기계로 확인된 적이 없었다**. `imports/` 의
덤프 둘은 사람이 손으로 뜬 근거 기록이지 검사가 아니고, 그래서 2026-09-05 실측에서
세만틱 4개·프리미티브 2개가 빠져 있고 다크 `primary` 두 개는 **값이 서로 달랐다**
(Figma 의 설명문은 `dark={indigo_700}` 이라 적혀 있는데 값만 `{peri_400}` 이었다 —
설계 변경이 아니라 Figma 쪽 실수라는 뜻이다). 근거: [[vinc.research.figma-code-sync-2026-09-05]]

이 스크립트가 그 대조다. **이름 대응표를 아래 MAP 한 곳에 못 박는 것**이 요점이라
별도 매핑 파일을 두지 않는다 — 소비자가 이 파일 하나뿐인데 둘로 나누면 서로 어긋날
자리만 생긴다.

## 쓰는 법

1. 라이브를 뜬다(에이전트가 Figma MCP `use_figma` 로, 아래 JS 그대로):

```js
const want = ["Primitive", "Semantic", "Scale", "Motion"];   // 아래 COLLECTIONS 와 같은 목록
const cols = (await figma.variables.getLocalVariableCollectionsAsync()).filter(c => want.includes(c.name));
const hex = c => "#" + [c.r,c.g,c.b].map(x => Math.round(x*255).toString(16).padStart(2,"0")).join("").toUpperCase();
const out = {};
for (const c of cols) {
  const modes = {}; for (const m of c.modes) modes[m.name] = {};
  for (const vid of c.variableIds) {
    const v = await figma.variables.getVariableByIdAsync(vid);
    if (!v) continue;
    for (const m of c.modes) {
      const raw = v.valuesByMode[m.modeId];
      let val;
      if (raw && raw.type === "VARIABLE_ALIAS") {
        const t = await figma.variables.getVariableByIdAsync(raw.id);
        val = "{" + (t ? t.name : raw.id) + "}";
      } else if (raw && typeof raw === "object" && "r" in raw) { val = hex(raw); }
      else { val = raw; }
      modes[m.name][v.name] = val;
    }
  }
  out[c.name] = modes;
}
const es = await figma.getLocalEffectStylesAsync();
return { variables: out, effectStyles: es.map(s => ({ name: s.name, effects: s.effects })) };
```

2. 그 반환값을 `imports/figma-live.json` 으로 저장하고 대조한다:

    python -X utf8 scripts/figma_diff.py                     # 보고서, 드리프트 있으면 exit 1
    python -X utf8 scripts/figma_diff.py --plan plan.json    # 고칠 것을 기계가 읽는 형태로

`--plan` 은 **Figma 를 코드에 맞추는** 작업 목록이다(반대 방향이 아니다). 적용은
에이전트가 `use_figma` 로 하며 이 스크립트는 Figma 에 쓰지 않는다.

## 대조 대상이 아닌 것 (드리프트가 아니라 계약이다)

- **Figma 에만**: `Graph`·`Window`·`Email`·`Domain`·`Locale` 컬렉션. 앞의 셋은
  `gen_theme_ts.py` 로만 코드에 닿고, `Locale` 은 카피라 정본이 사이트고, `Domain` 은
  코드가 팔레트 대신 골든앵글 생성 모델을 쓴다(모델 자체가 달라 값 대조가 성립 안 한다).
- **Figma 에만 · Scale**: `icon/app`(18) `icon/base`(24) `layout/lnb-w`(232)
  `layout/gnb-h`(52). ⚠ 이건 Figma 가 틀린 게 아니라 **코드가 허브 밖에 두고 있는 것**이다
  — `app/src/app.css` 의 `--icon-size: 18px` · `--lnb-w: 14.5rem` · GNB `3.25rem` 과 값이
  전부 같다. 승격은 별건이라 여기선 제외로 둔다.
- **SSoT 에만**: `lh`·`font` — Figma 는 텍스트 스타일로 들고 있어 변수 대조가 성립 안 한다.
- **부분 대조**: `fs/xl`·`fs/display` 는 SSoT 가 `clamp()` 라 Figma 는 그 **상한**만 담는다.
  상한끼리 비교한다.
- `shadow.elev-0` 은 `none` 이라 이펙트 스타일로 만들 수 없다(효과 0개짜리 스타일은
  의미가 없다). 제외.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

HUB = pathlib.Path(__file__).resolve().parent.parent

#: 컬렉션 이름 ↔ SSoT 그룹. 모드 이름 ↔ SSoT 모드 키(None = 모드 없는 단일 값).
COLLECTIONS = {
    "Primitive": {"modes": {"Value": None}, "kind": "primitive"},
    "Semantic": {"modes": {"Light": "light", "Dark": "dark"}, "kind": "semantic"},
    "Scale": {"modes": {"Value": None}, "kind": "scale"},
    "Motion": {"modes": {"Value": None}, "kind": "motion"},
}

#: Scale 에서 대조하지 않는 Figma 전용 키(위 docstring 참조).
SCALE_IGNORE = {"icon/app", "icon/base", "layout/lnb-w", "layout/gnb-h"}

#: 세만틱 변수를 새로 만들 때 붙일 scopes. 기존 형제들과 같은 관례
#: (면=FRAME/SHAPE_FILL · 텍스트=TEXT_FILL · 선=STROKE_COLOR · 링=EFFECT_COLOR).
SEMANTIC_SCOPES = {
    "focus": ["STROKE_COLOR", "EFFECT_COLOR"],
    "logo-tip": ["SHAPE_FILL"],
    "titlebar-close": ["FRAME_FILL", "SHAPE_FILL"],
    "titlebar-close-foreground": ["SHAPE_FILL", "TEXT_FILL"],
}
DEFAULT_SEMANTIC_SCOPES = ["FRAME_FILL", "SHAPE_FILL", "TEXT_FILL"]


def _clean(d: dict) -> dict:
    """토큰만 남긴다. `$`-접두 주석과, `motion`·`viewScene`·`topicJewels` 가 쓰는 **접두 없는
    `note`** 를 뺀다 — 그 셋은 DTCG 관례를 안 따라서 `$note` 가 아니라 `note` 다(빼지 않으면
    산문 한 문단이 토큰으로 둔갑해 Figma 에 변수로 생긴다)."""
    return {k: v for k, v in d.items() if not k.startswith("$") and k != "note"}


def _px(v):
    """rem/px 문자열을 px 수로. clamp() 는 상한만 뽑는다(Figma 는 유동값을 못 담는다)."""
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if s.startswith("clamp("):
        s = s.rsplit(",", 1)[1].strip().rstrip(")")
    if s.endswith("rem"):
        return float(s[:-3]) * 16
    if s.endswith("px"):
        return float(s[:-2])
    return float(s)


def _ms(v: str) -> float:
    """'.24s' → 240. Figma 는 duration 변수형이 없어 FLOAT(밀리초)로 담는다."""
    s = str(v).strip()
    if s.endswith("ms"):
        return float(s[:-2])
    if s.endswith("s"):
        return round(float(s[:-1]) * 1000, 3)
    return float(s)


_SHADOW_RE = re.compile(
    r"^(-?[\d.]+)(?:px)?\s+(-?[\d.]+)px\s+(-?[\d.]+)px(?:\s+(-?[\d.]+)px)?\s+"
    r"hsl\(\s*([\d.]+)\s+([\d.]+)%\s+([\d.]+)%\s*/\s*([\d.]+)\s*\)$")


def _hsl_to_rgb(h: float, s: float, l: float) -> tuple:
    s, l = s / 100.0, l / 100.0
    c = (1 - abs(2 * l - 1)) * s
    x = c * (1 - abs((h / 60.0) % 2 - 1))
    m = l - c / 2
    r, g, b = [(c, x, 0), (x, c, 0), (0, c, x), (0, x, c), (x, 0, c), (c, 0, x)][int(h // 60) % 6]
    return (round(r + m, 4), round(g + m, 4), round(b + m, 4))


def parse_shadow(css: str):
    """CSS box-shadow 한 줄 → Figma DROP_SHADOW 형태. 못 읽으면 None."""
    m = _SHADOW_RE.match(css.strip())
    if not m:
        return None
    x, y, blur, spread, h, s, l, a = m.groups()
    r, g, b = _hsl_to_rgb(float(h), float(s), float(l))
    return {"x": float(x), "y": float(y), "radius": float(blur),
            "spread": float(spread or 0), "color": {"r": r, "g": g, "b": b, "a": float(a)}}


def expected(tokens: dict) -> dict:
    """SSoT → Figma 가 담아야 할 값. 정본은 코드이므로 이쪽이 언제나 기대값이다."""
    prim = {k: v["$value"].upper() for k, v in _clean(tokens["primitive"]).items()}
    sem = {mode: dict(_clean(tokens["semantic"][mode])) for mode in ("light", "dark")}
    scale = {}
    for k, v in _clean(tokens["radius"]).items():
        scale["radius/" + ("default" if k == "DEFAULT" else k)] = _px(v)
    for k, v in _clean(tokens["space"]).items():
        scale["space/" + k] = _px(v)
    for k, v in _clean(tokens["fs"]).items():
        scale["fs/" + k] = _px(v)
    motion = {}
    for k, v in _clean(tokens.get("motion", {})).items():
        motion[k] = _ms(v) if k.startswith("dur-") else v
    effects = {}
    for k, v in _clean(tokens["shadow"]).items():
        if v == "none":
            continue
        effects["shadow/" + k] = parse_shadow(v)
    for k, v in _clean(tokens.get("shadowDark", {})).items():
        effects["shadow/dark-" + k] = parse_shadow(v)
    return {"primitive": prim, "semantic": sem, "scale": scale, "motion": motion, "effects": effects}


def _live_effect(effects: list):
    """이펙트 스타일의 첫 DROP_SHADOW 를 비교 가능한 형태로. 그림자가 아니면 None."""
    for e in effects:
        if e.get("type") == "DROP_SHADOW":
            c = e.get("color", {})
            return {"x": float(e["offset"]["x"]), "y": float(e["offset"]["y"]),
                    "radius": float(e["radius"]), "spread": float(e.get("spread", 0)),
                    "color": {"r": round(c.get("r", 0), 4), "g": round(c.get("g", 0), 4),
                              "b": round(c.get("b", 0), 4), "a": round(c.get("a", 1), 4)}}
    return None


def _same_shadow(a: dict, b: dict) -> bool:
    if a is None or b is None:
        return a == b
    for k in ("x", "y", "radius", "spread"):
        if abs(a[k] - b[k]) > 0.5:
            return False
    for ch in ("r", "g", "b", "a"):
        if abs(a["color"][ch] - b["color"][ch]) > 0.01:
            return False
    return True


def compare(exp: dict, live: dict) -> dict:
    """기대(코드) vs 라이브(Figma). ops 는 **Figma 를 고치는** 작업이다."""
    v = live.get("variables", {})
    ops = {"create_collection": [], "create_variable": [], "set_value": [],
           "create_effect_style": [], "set_effect_style": []}
    info = {"figma_only": []}
    #: 두 모드를 도는 동안 같은 변수의 생성이 두 번 잡히지 않게 (coll, name) 로 모은다.
    #: 변수는 하나이고 모드는 그 안의 값이므로, 생성 작업도 하나여야 한다.
    pending: dict = {}

    def cmp_map(coll: str, mode: str, want: dict, got: dict, vtype: str, ignore=frozenset()):
        for name, val in want.items():
            if name not in got:
                key = (coll, name)
                if key not in pending:
                    # MIXED = 값이 타입을 정한다(Motion: 커브는 STRING, 지속시간은 FLOAT ms).
                    t = vtype if vtype != "MIXED" else ("FLOAT" if isinstance(val, (int, float)) else "STRING")
                    pending[key] = {"collection": coll, "name": name, "type": t, "values": {},
                                    "scopes": SEMANTIC_SCOPES.get(name, DEFAULT_SEMANTIC_SCOPES)
                                    if coll == "Semantic" else None}
                    ops["create_variable"].append(pending[key])
                pending[key]["values"][mode] = val
            elif got[name] != val:
                ops["set_value"].append({"collection": coll, "name": name, "mode": mode,
                                         "from": got[name], "to": val})
        for name in got:
            if name not in want and name not in ignore:
                info["figma_only"].append(f"{coll}/{mode}/{name} = {got[name]}")

    # Primitive · Scale · Motion — 단일 모드
    cmp_map("Primitive", "Value", exp["primitive"], v.get("Primitive", {}).get("Value", {}), "COLOR")
    cmp_map("Scale", "Value", exp["scale"], v.get("Scale", {}).get("Value", {}), "FLOAT", SCALE_IGNORE)
    if exp["motion"]:
        if "Motion" not in v:
            ops["create_collection"].append({"name": "Motion", "modes": ["Value"]})
        cmp_map("Motion", "Value", exp["motion"], v.get("Motion", {}).get("Value", {}), "MIXED")

    # Semantic — 두 모드
    for mode, fmode in (("light", "Light"), ("dark", "Dark")):
        cmp_map("Semantic", fmode, exp["semantic"][mode], v.get("Semantic", {}).get(fmode, {}), "COLOR")

    # 이펙트 스타일(그림자)
    got_es = {s["name"]: _live_effect(s.get("effects", [])) for s in live.get("effectStyles", [])}
    for name, want in exp["effects"].items():
        if name not in got_es:
            ops["create_effect_style"].append({"name": name, "shadow": want})
        elif not _same_shadow(want, got_es[name]):
            ops["set_effect_style"].append({"name": name, "from": got_es[name], "to": want})

    return {"ops": ops, "info": info}


def main() -> int:
    ap = argparse.ArgumentParser(description="Figma 라이브 덤프를 토큰 SSoT 와 대조(정본=코드)")
    ap.add_argument("--live", default="imports/figma-live.json")
    ap.add_argument("--tokens", default="DESIGN/design-tokens.json")
    ap.add_argument("--plan", help="고칠 것을 JSON 으로 저장할 경로")
    a = ap.parse_args()

    live_p = HUB / a.live
    if not live_p.exists():
        print(f"[ERROR] 라이브 덤프가 없다: {live_p}\n"
              f"        docstring 의 JS 를 use_figma 로 돌려 저장하라.", file=sys.stderr)
        return 3
    tokens = json.loads((HUB / a.tokens).read_text(encoding="utf-8"))
    live = json.loads(live_p.read_text(encoding="utf-8"))
    res = compare(expected(tokens), live)
    ops, info = res["ops"], res["info"]

    total = sum(len(x) for x in ops.values())
    for coll in ops["create_collection"]:
        print(f"[CREATE·컬렉션] {coll['name']} (modes={coll['modes']})")
    for o in ops["create_variable"]:
        vals = " · ".join(f"{m}={x}" for m, x in o["values"].items())
        print(f"[CREATE] {o['collection']}/{o['name']} ({o['type']}) {vals}")
    for o in ops["set_value"]:
        print(f"[FIX]    {o['collection']}/{o['mode']}/{o['name']}: {o['from']} → {o['to']}")
    for o in ops["create_effect_style"]:
        print(f"[CREATE·이펙트] {o['name']}")
    for o in ops["set_effect_style"]:
        print(f"[FIX·이펙트]    {o['name']}")
    for line in info["figma_only"]:
        print(f"(Figma 에만 · 판정 필요) {line}")

    extra = len(info["figma_only"])
    head = "일치" if not total else f"Figma 에서 고칠 것 {total}건"
    tail = f" · Figma 에만 있는 것 {extra}건(판정 필요)" if extra else ""
    print(f"\n{head}{tail}")
    if a.plan:
        (HUB / a.plan).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"계획 → {HUB / a.plan}")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
