// 살아 있는 DOM 추출(JSON)을 Figma 편집 레이어로 옮긴다. `extract_overlays.mjs` 가 만든 JSON 을
// `JOBS` 에 [TARGET(비울 변형·컴포넌트 id), J] 쌍으로 붙이고 `PAGE` 를 채워 use_figma 로 통째로 실행한다.
// 코드 한도가 50K자라 작은 것은 여러 개를 한 호출에, 큰 시트는 하나씩 보낸다.
// html-to-design(generate_figma_design) 이 세션에 없을 때 쓰는 대체 경로다(2026-09-10).
//
// 규칙(실측으로 물린 것):
//  - 텍스트는 Inter 로 만들고 마지막에 Pretendard 텍스트 스타일을 setTextStyleIdAsync 로 건다.
//    Pretendard 는 loadFontAsync 가 거부되므로 characters·textAutoResize 는 스타일 적용 **전에** 끝낸다.
//  - 한 줄 글자는 자동 폭(WIDTH_AND_HEIGHT). 오른쪽 끝에 붙어 있고 상자가 글자 폭에 딱 맞을 때만
//    오른쪽 끝을 지킨다(폭이 넓은 왼쪽 정렬 문단을 오른쪽으로 밀지 않게).
//  - 색은 다크 토큰 hex 와 맞으면 Semantic 변수에 바인딩한다. setBoundVariableForPaint 는 opacity 를
//    1 로 되돌리므로 바인딩 뒤 opacity 를 다시 넣는다. 대상 세트에는 Semantic=Dark 명시 모드가 있어야 한다.
//  - <input type=range> 는 트랙+손잡이로, 체크박스(t:'cb')는 채워진 사각으로 그린다.
//  - 세트 안의 변형은 maxHeight 가 걸려 있을 수 있다(캡처 승격 유산) → null 로 풀고 resize.
//  - 시트·패널의 스크롤 영역은 추출 때 펼쳐 두므로 루트는 clipsContent 로 잘라 앱과 같은 높이를 유지한다.
const PAGE = "619:1027";      // 05 · Overlays (문서 열·LNB 는 03 · App Shell 619:1026)
const JOBS = [];              // [["942:1767", {…extract_overlays.mjs 출력…}], …]
const page = await figma.getNodeByIdAsync(PAGE);
await figma.setCurrentPageAsync(page);
for (const s of ["Regular", "Medium", "Semi Bold", "Bold", "Italic"]) await figma.loadFontAsync({ family: "Inter", style: s });
let MONO = false; try { await figma.loadFontAsync({ family: "JetBrains Mono", style: "Regular" }); MONO = true; } catch (e) {}
// 다크 토큰 hex → Semantic 변수(2026-09-10 파일 기준)
const VAR = { "#0c0d13": "VariableID:222:6", "#e8e7ef": "VariableID:222:7", "#191c29": "VariableID:222:8", "#12141c": "VariableID:222:10", "#a7a9bf": "VariableID:222:11", "#a6a8bf": "VariableID:222:11", "#6e7189": "VariableID:222:12", "#6d7088": "VariableID:222:12", "#25293c": "VariableID:222:13", "#8f96d9": "VariableID:222:15", "#9198d9": "VariableID:222:15" };
const vc = {}; const getVar = async (id) => vc[id] || (vc[id] = await figma.variables.getVariableByIdAsync(id));
const parse = (s) => { const m = /rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+)(?:,\s*([\d.]+))?\)/.exec(s || ''); return m ? { r: +m[1] / 255, g: +m[2] / 255, b: +m[3] / 255, a: m[4] === undefined ? 1 : +m[4] } : null; };
const hex = (c) => '#' + [c.r, c.g, c.b].map(v => Math.round(v * 255).toString(16).padStart(2, '0')).join('');
const paint = async (s) => { const c = parse(s); if (!c || c.a === 0) return null; const h = hex(c); let p = { type: 'SOLID', color: { r: c.r, g: c.g, b: c.b }, opacity: c.a }; if (VAR[h]) p = figma.variables.setBoundVariableForPaint(p, 'color', await getVar(VAR[h])); if (p.opacity !== c.a) p = { ...p, opacity: c.a }; return p; };
// 크기 → [Regular, Medium, Semibold, Bold] Pretendard 텍스트 스타일 id
const TS = { 11: ["S:9c80c7a50d34adebe635e291a331863da8c99c9e,", "S:e83134111b1c38f687e8ffa541f44de9633d8807,", "S:e83134111b1c38f687e8ffa541f44de9633d8807,", "S:e83134111b1c38f687e8ffa541f44de9633d8807,"], 12: ["S:6778725574c52313e6ac3fe326b9b57480352f47,", "S:4ec75bf2876e93ac5ba223cf6153aa5514bba361,", "S:4ec75bf2876e93ac5ba223cf6153aa5514bba361,", "S:4ec75bf2876e93ac5ba223cf6153aa5514bba361,"], 13: ["S:a8811e8bf755a5377f688abf3eee2107e7d3d1df,", "S:7ebcb8370b84f91051e23613cccd84d6c7a81194,", "S:ce6efec6320ee46329532a1d8c168192bb121105,", "S:ce6efec6320ee46329532a1d8c168192bb121105,"], 14: ["S:816f9aca10c35a968eccfaa8fba558b3ae46bb64,", "S:e23616e6623cb27ef3262b4c74be2c0ecd5ecb60,", "S:ad08f43185949a73d0bb956c98a0722694547517,", "S:ad08f43185949a73d0bb956c98a0722694547517,"], 15: ["S:9ae1d26d0ffc79a6acd4fed76c642896fae1883d,", "S:03dc16965e4631d6861f08f70dee044bdeb88578,", "S:f50520751194d33f3d8e80cc8ec3a1e97403520b,", "S:e897e43a0cdb1f51283be1c76c6241d615cbe577,"], 17: ["S:1b78b0cc8905361a0434d9f6b9e880c72b21f106,", "S:1b78b0cc8905361a0434d9f6b9e880c72b21f106,", "S:fd6bad19199ad1292ec3ec4686f405094d523f4d,", "S:fd6bad19199ad1292ec3ec4686f405094d523f4d,"], 20: ["S:4f99c0a4213456d56a97581f0ba71e74696ce779,", "S:4f99c0a4213456d56a97581f0ba71e74696ce779,", "S:4f99c0a4213456d56a97581f0ba71e74696ce779,", "S:4f99c0a4213456d56a97581f0ba71e74696ce779,"], 24: ["S:d2ae18042ba76fab6120b51f77fe8ae7b78644cd,", "S:d2ae18042ba76fab6120b51f77fe8ae7b78644cd,", "S:d2ae18042ba76fab6120b51f77fe8ae7b78644cd,", "S:d2ae18042ba76fab6120b51f77fe8ae7b78644cd,"], 32: ["S:e3f01f04d9dc786d0067caac7f817e9447c6927d,", "S:e3f01f04d9dc786d0067caac7f817e9447c6927d,", "S:e3f01f04d9dc786d0067caac7f817e9447c6927d,", "S:e3f01f04d9dc786d0067caac7f817e9447c6927d,"] };
const MONO_STYLE = "S:51585d9a81000fae026f51766acfa29999fe565a,";
const styleFor = (size, weight, mono) => { if (mono) return MONO_STYLE; const sizes = Object.keys(TS).map(Number); let best = sizes[0]; for (const s of sizes) if (Math.abs(s - size) < Math.abs(best - size)) best = s; const w = +weight >= 700 ? 3 : +weight >= 600 ? 2 : +weight >= 500 ? 1 : 0; return TS[best][w]; };
const stats = { frames: 0, texts: 0, svgs: 0, sliders: 0, cbs: 0, styleErr: 0, svgErr: 0 };
const styleBox = async (f, n, W, H) => { const bgp = n.bg ? await paint(n.bg) : null; f.fills = bgp ? [bgp] : []; if (n.bd) { const p = await paint(n.bd[1]); if (p) { f.strokes = [p]; f.strokeWeight = n.bd[0]; f.strokeAlign = 'INSIDE'; } } else if (n.bb) { const p = await paint(n.bb[1]); if (p) { f.strokes = [p]; f.strokeAlign = 'INSIDE'; f.strokeTopWeight = 0; f.strokeLeftWeight = 0; f.strokeRightWeight = 0; f.strokeBottomWeight = n.bb[0]; } } if (n.r) f.cornerRadius = Math.min(n.r, Math.min(W, H) / 2); if (n.o !== undefined) f.opacity = n.o; if (n.f) f.effects = [{ type: 'BACKGROUND_BLUR', radius: 14, visible: true }]; };
const build = async (n, parent, ox, oy, pw) => {
  const [x, y, w, h] = n.b; const W = Math.max(1, w), H = Math.max(1, h);
  if (n.t === 'cb') {   // 체크박스·라디오: on 이면 ring 채움 + 표식, off 면 border 만(앱 accent-color 규칙과 같다)
    const radio = n.kind === 'radio'; const c = radio ? figma.createEllipse() : figma.createFrame(); c.name = (radio ? 'radio' : 'checkbox') + (n.on ? ' · on' : ' · off'); parent.appendChild(c); c.x = x - ox; c.y = y - oy; c.resize(13, 13); if (!radio) c.cornerRadius = 3;
    if (n.on) { c.fills = [await paint('rgb(145, 152, 217)')]; if (radio) { const d = figma.createEllipse(); d.name = 'dot'; parent.appendChild(d); d.resize(5, 5); d.x = x - ox + 4; d.y = y - oy + 4; d.fills = [await paint('rgb(25, 28, 41)')]; } else { const k = figma.createNodeFromSvg('<svg viewBox="0 0 13 13"><path d="M3 6.8 5.6 9.3 10 4.4" fill="none" stroke="#191c29" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>'); c.appendChild(k); k.x = 0; k.y = 0; k.resize(13, 13); } }
    else { c.fills = [await paint('rgb(12, 13, 19)')]; c.strokes = [await paint('rgb(110, 113, 137)')]; c.strokeWeight = 1; c.strokeAlign = 'INSIDE'; }
    stats.cbs++; return; }
  if (n.t === 'svg') { let node; try { node = figma.createNodeFromSvg(n.svg.replace(/currentColor/g, n.col || '#ffffff').replace(/hsl\(var\(--ring\)\)/g, '#8f96d9')); } catch (e) { stats.svgErr++; return; } parent.appendChild(node); node.name = (n.c ? '.' + n.c : 'svg'); node.resize(W, H); node.x = x - ox; node.y = y - oy; const p = await paint(n.col); if (p) for (const v of node.findAll(k => 'strokes' in k)) { if (v.strokes.length) v.strokes = [{ ...p, opacity: v.strokes[0].opacity }]; if (v.fills.length && v.fills[0].type === 'SOLID' && hex(v.fills[0].color) !== '#8f96d9' && hex(v.fills[0].color) !== '#ffffff') v.fills = [{ ...p, opacity: v.fills[0].opacity }]; } stats.svgs++; return; }
  if (n.t === 'input' && (n.c || '').includes('slider')) {
    const g = figma.createFrame(); g.name = '#' + (n.i || 'slider'); parent.appendChild(g); g.x = x - ox; g.y = y - oy; g.resize(W, H); g.fills = []; g.clipsContent = false;
    const track = figma.createRectangle(); track.name = 'track'; track.resize(W, 4); track.x = 0; track.y = (H - 4) / 2; track.cornerRadius = 2; track.fills = [await paint('rgb(37, 41, 60)')]; g.appendChild(track);
    const ratio = n.i === 'pref-when' ? 1 : 0.4; const th = figma.createEllipse(); th.name = 'thumb'; th.resize(14, 14); th.x = Math.round((W - 14) * ratio); th.y = (H - 14) / 2; th.fills = [await paint('rgb(145, 152, 217)')]; g.appendChild(th);
    stats.sliders++; return;
  }
  if (n.t === '#text' || (n.x !== undefined && !n.k)) {
    let box = null;
    if (n.t !== '#text' && (n.bg || n.bd || n.bb || n.r)) { box = figma.createFrame(); box.name = (n.i ? '#' + n.i : n.c ? '.' + n.c : n.t).slice(0, 50); parent.appendChild(box); box.x = x - ox; box.y = y - oy; box.resize(W, H); await styleBox(box, n, W, H); box.clipsContent = true; stats.frames++; }
    const F = n.F || ['Inter', 13, '400', 'rgb(232,231,239)', 'left', 0, 0]; const mono = MONO && /mono|jetbrains|menlo|consolas/i.test(F[0]); const wgt = +F[2] || 400; const style = F[6] ? 'Italic' : (wgt >= 700 ? 'Bold' : wgt >= 600 ? 'Semi Bold' : wgt >= 500 ? 'Medium' : 'Regular');
    const t = figma.createText(); t.fontName = mono ? { family: 'JetBrains Mono', style: 'Regular' } : { family: 'Inter', style }; t.characters = n.x || ''; t.fontSize = Math.max(6, F[1]);
    t.textAlignHorizontal = F[4] === 'center' ? 'CENTER' : F[4] === 'right' || F[4] === 'end' ? 'RIGHT' : 'LEFT'; t.textAlignVertical = n.t === 'textarea' || n.t === 'pre' ? 'TOP' : 'CENTER';
    const p = await paint(F[3]); if (p) t.fills = [p]; if (F[5]) t.lineHeight = { value: F[5], unit: 'PIXELS' };
    (box || parent).appendChild(t); t.name = (n.i ? '#' + n.i : n.c ? '.' + n.c : 'text').slice(0, 40); t.textAutoResize = 'NONE';
    if (box) { const pad = (n.t === 'input' || n.t === 'textarea') ? 12 : 0; const padY = n.t === 'textarea' ? 10 : 0; t.x = pad; t.y = padY; t.resize(Math.max(1, W - pad * 2), Math.max(1, H - padY * 2)); } else { t.x = x - ox; t.y = y - oy; t.resize(W, H); }
    const single = !box && F[5] && H < F[5] * 1.6 && !(n.x || '').includes('\n');
    if (single && !n.ov) { const right = (x + W) - ox; t.textAutoResize = 'WIDTH_AND_HEIGHT'; const tight = W < t.width + 12; const nearRight = pw && right > pw - 24; if (t.textAlignHorizontal === 'RIGHT' || (tight && nearRight && (x - ox) > 24)) t.x = right - t.width;   /* 왼쪽이 부모에 붙은 꽉 찬 블록(p.hint 등)은 오른쪽 고정이 아니다 — 2026-09-10 실측: Pretendard 폭 > 상자 폭이면 왼쪽으로 삐져나갔다 */ else if (t.textAlignHorizontal === 'CENTER') t.x = (x - ox) + (W - t.width) / 2; t.y = (y - oy) + (H - t.height) / 2; }
    try { await t.setTextStyleIdAsync(styleFor(F[1], F[2], mono)); } catch (e) { stats.styleErr++; }
    stats.texts++; return;
  }
  const f = figma.createFrame(); f.name = [n.t, n.i ? '#' + n.i : '', n.c ? '.' + n.c : ''].join('').slice(0, 60); parent.appendChild(f); f.x = x - ox; f.y = y - oy; f.resize(W, H);
  await styleBox(f, n, W, H); f.clipsContent = !!n.ov || /sheet-body|lens-scroll/.test(n.c || ''); stats.frames++;   // 펼쳐서 잰 스크롤 영역은 제 상자에서 자른다(발 아래로 줄이 새지 않게)
  for (const k of (n.k || [])) await build(k, f, x, y, W);
  return f;
};
const out = [];
for (const [TARGET, J] of JOBS) {
  const T = await figma.getNodeByIdAsync(TARGET);
  for (const c of [...T.children]) c.remove();
  const H = J.vh || J.b[3];   // vh = 펼치기 전 앱 높이(추출기가 잰다) — 루트를 이 높이로 잘라 앱과 같게 둔다
  T.layoutMode = 'NONE'; T.maxHeight = null; T.minHeight = null; try { T.layoutSizingHorizontal = 'FIXED'; T.layoutSizingVertical = 'FIXED'; } catch (e) {} T.resize(J.b[2], H); await styleBox(T, J, J.b[2], H); T.clipsContent = true;
  T.effects = [{ type: 'DROP_SHADOW', color: { r: 0, g: 0, b: 0, a: 0.32 }, offset: { x: 0, y: 24 }, radius: 48, spread: 0, visible: true, blendMode: 'NORMAL' }];
  if (J.x !== undefined && !J.k) await build({ ...J, t: '#text', b: [J.b[0] + 10, J.b[1], J.b[2] - 20, J.b[3]] }, T, J.b[0], J.b[1], J.b[2]);   // 루트가 곧 글자인 것(툴팁)
  for (const k of (J.k || [])) await build(k, T, J.b[0], J.b[1], J.b[2]);
  out.push({ id: T.id, size: [T.width, T.height] });
}
return { mutatedNodeIds: out.map(o => o.id), out, stats };
