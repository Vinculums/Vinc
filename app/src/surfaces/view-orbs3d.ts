/** Vinc orb — 3D 노드 몸통 한 벌.
 *
 *  모든 종이 **같은 유리와 안의 빛**을 쓰고 껍질의 모양만 다르다:
 *  개념 = 빛의 구름을 담은 유리구 · 결정 = 깎인 결정을 담은 유리구 · 기록 = 유리 정사면체 ·
 *  사건 = 유리 정팔면체 · 다리 = 유리 조각(격자 틈을 잇는 면) · 주제 = 속이 빈 점 껍질.
 *  상태 다섯: idle · active · selected · historical · conflict.
 *
 *  그리는 법: 노드마다 카메라를 향한 사각형 하나를 인스턴싱하고, 프래그먼트에서 광선을 쏴
 *  구(해석해)나 볼록 다면체(면 평면과의 교차)와 만나는 구간을 레이마칭한다. 다면체는
 *  **카메라에 고정된 자세**라 궤도를 돌아도 실루엣이 안 바뀐다(월드에 고정하면 각도마다
 *  삼각이 마름모로 읽힌다). 메시가 하나라 드로우콜은 패스당 1이다.
 *
 *  패스 둘: 몸통(불투명, 깊이 씀)과 바깥(후광·선택 고리·주제 껍질, 깊이 안 씀). 한 메시
 *  안은 깊이 정렬이 안 되므로 몸통을 투명으로 두면 궤도를 돌 때 앞뒤가 뒤집혀 보인다.
 *  근거: [[vinc.research.graph-vinc-orb-2026-10-28]] */
import * as THREE from "three";

export const ORB_STATE = { idle: 0, active: 1, selected: 2, historical: 3, conflict: 4 } as const;
export type OrbState = typeof ORB_STATE[keyof typeof ORB_STATE];

/** 노드 종 → 셰이더 종 번호. 엔티티 등 이름 없는 종은 기록과 같은 사면체로 간다
 *  (예전 렌더러도 개념·결정·다리·주제 밖은 전부 문서 사면체였다). */
export function orbKindOf(kind: string | undefined): number {
  switch (kind) {
    case "concept": case undefined: case "": return 0;
    case "decision": return 1;
    case "episode": return 3;
    case "bridge": return 4;
    case "domain": case "topic": return 5;
    default: return 2;
  }
}
/** 종별 크기 보정 — 다면체는 꼭짓점이 구 밖으로 나가므로 같은 반경이면 더 커 보인다. */
const KIND_SCALE: Record<number, number> = { 2: .85, 3: .9, 4: .72 };

const VERT = /* glsl */`
attribute vec3 aCenter; attribute float aRadius; attribute float aKind; attribute float aState; attribute float aSeed; attribute vec3 aHue;
uniform float uPxUnit;
varying vec3 vPos; varying vec3 vC; varying float vR; varying float vKind; varying float vState; varying float vSeed; varying vec3 vHue; varying float vPx;
void main(){
  vec4 cv = modelViewMatrix * vec4(aCenter, 1.0);
  // 다면체의 꼭짓점과 선택 고리가 들어갈 여유
  float pad = aKind > 1.5 && aKind < 2.5 ? 1.62 : aKind > 3.5 && aKind < 4.5 ? 2.15 : 1.5;
  vec3 toCam = normalize(-cv.xyz);
  vec3 base = cv.xyz + toCam * aRadius;                       // 구 앞에 세워 깊이에 안 잘리게
  float s = aRadius * pad * (length(base) / max(length(cv.xyz), 1e-4));
  vec3 right = normalize(cross(vec3(0.0, 1.0, 0.0), toCam)); vec3 up = cross(toCam, right);
  vec3 p = base + (right * position.x + up * position.y) * s * 2.0;
  vPos = p; vC = cv.xyz; vR = aRadius; vKind = aKind; vState = aState; vSeed = aSeed; vHue = aHue;
  vPx = aRadius * uPxUnit / max(-cv.z, 1e-4);                // 화면 반경(px) — 작으면 레이마칭을 건너뛴다
  gl_Position = projectionMatrix * vec4(p, 1.0);
}`;

const FRAG = /* glsl */`
precision highp float;
uniform float uTime; uniform float uSteps; uniform float uLight; uniform float uPass;
varying vec3 vPos; varying vec3 vC; varying float vR; varying float vKind; varying float vState; varying float vSeed; varying vec3 vHue; varying float vPx;
float hash(vec3 p){ p = fract(p * 0.3183099 + 0.1); p *= 17.0; return fract(p.x * p.y * p.z * (p.x + p.y + p.z)); }
float noise(vec3 x){ vec3 i = floor(x), f = fract(x); f = f * f * (3.0 - 2.0 * f);
  return mix(mix(mix(hash(i), hash(i + vec3(1,0,0)), f.x), mix(hash(i + vec3(0,1,0)), hash(i + vec3(1,1,0)), f.x), f.y),
             mix(mix(hash(i + vec3(0,0,1)), hash(i + vec3(1,0,1)), f.x), mix(hash(i + vec3(0,1,1)), hash(i + vec3(1,1,1)), f.x), f.y), f.z); }
float fbm(vec3 p){ float a = 0.5, s = 0.0; for (int i = 0; i < 4; i++){ s += a * noise(p); p = p * 2.03 + 1.7; a *= 0.5; } return s; }
mat3 rotY(float a){ float c = cos(a), s = sin(a); return mat3(c,0.,-s, 0.,1.,0., s,0.,c); }
mat3 rotX(float a){ float c = cos(a), s = sin(a); return mat3(1.,0.,0., 0.,c,s, 0.,-s,c); }
mat3 rotZ(float a){ float c = cos(a), s = sin(a); return mat3(c,s,0., -s,c,0., 0.,0.,1.); }
vec3 desat(vec3 c, float k){ float l = dot(c, vec3(0.299, 0.587, 0.114)); return mix(c, vec3(l), k); }
float solid = 0.0;   // 몸통 패스가 그린 픽셀인가 — 두 패스가 같은 판정을 읽는다

// 유리 몸통. 라이트에서는 어두운 구슬이 되지 않게 몸통을 그 색 쪽으로 밝힌다.
vec3 glassBody(vec3 hue, float ndv){
  vec3 dark = mix(vec3(0.02, 0.025, 0.04), hue * 0.16, 0.7) + hue * 0.10 * pow(ndv, 2.0);
  vec3 lit = mix(hue, vec3(1.0), 0.62) * (0.62 + 0.25 * ndv);
  return mix(dark, lit, uLight * 0.7);
}

// 구 안의 것 — 개념은 떠도는 빛의 구름(아직 굳지 않은 생각), 결정은 가운데 멈춘 결정(굳은 것)
vec4 inner(vec3 q, float kind, vec3 hue, float t, float seed, float energy){
  vec3 light = mix(hue, vec3(1.0), 0.55), deep = hue * 0.35;
  if (kind < 0.5) {
    vec3 w = vec3(fbm(q * 1.7 + seed * 7.0 + t * 0.06), fbm(q * 1.7 + 3.1 + t * 0.05), fbm(q * 1.7 + 5.2 - t * 0.04));
    float n = fbm(q * 2.1 + w * 1.6 + seed * 3.0);
    float d = smoothstep(0.30, 0.70, n) * (1.0 - dot(q, q) * 0.35);
    vec3 c = mix(hue * 0.7, light, smoothstep(0.42, 0.75, n)) + vec3(1.0) * smoothstep(0.66, 0.85, n) * 0.9;
    return vec4(c * (1.6 + energy * 1.5), d * 1.6);
  }
  float o = abs(q.x) + abs(q.y * 0.8) + abs(q.z);
  float core = 1.0 - smoothstep(0.40, 0.43, o);
  float facet = 0.55 + 0.45 * step(0.0, q.x * q.y) * step(0.0, q.z + 0.1);
  float glow = exp(-o * o * 6.0) * 0.55;
  float haze = smoothstep(0.55, 0.8, fbm(q * 2.5 + seed * 5.0)) * 0.25;
  vec3 c = mix(light, vec3(1.0, 0.97, 0.9), 0.6) * (core * facet * 1.6 + glow) + deep * haze;
  return vec4(c * (0.9 + energy), core * 2.5 + glow + haze);
}

// 단위구 위의 위경도 점 격자(반지름은 라디안)
float dotShell(vec3 n, float rings, float rad){
  float lat = asin(clamp(n.y, -1.0, 1.0)), lon = atan(n.z, n.x);
  float fl = (lat + 1.5707963) / 3.1415927 * rings; float ri = floor(fl + 0.5); float latc = ri / rings * 3.1415927 - 1.5707963;
  float cnt = max(1.0, floor(cos(latc) * rings * 2.0 + 0.5)); float fo = (lon + 3.1415927) / 6.2831853 * cnt; float lonc = (floor(fo) + 0.5) / cnt * 6.2831853 - 3.1415927;
  float dl = lat - latc, dn = (lon - lonc) * cos(lat); return 1.0 - smoothstep(rad * 0.6, rad, length(vec2(dl, dn)));
}

// ── 유리 다면체: 기록(정사면체) · 사건(정팔면체) · 다리(길쭉한 조각) ──
vec4 polyPlane(float kind, int i){
  float fi = float(i);
  if (kind < 2.5) { vec3 n = fi < 0.5 ? vec3(-1.,-1.,-1.) : fi < 1.5 ? vec3(-1.,1.,1.) : fi < 2.5 ? vec3(1.,-1.,1.) : vec3(1.,1.,-1.); return vec4(normalize(n), 1.32 / 3.0); }
  float sx = mod(fi, 2.0) < 0.5 ? 1.0 : -1.0, sy = mod(floor(fi / 2.0), 2.0) < 0.5 ? 1.0 : -1.0, sz = fi < 3.5 ? 1.0 : -1.0;
  if (kind < 3.5) return vec4(normalize(vec3(sx, sy, sz)), 1.18 / sqrt(3.0));
  vec3 v = vec3(sx / (sx > 0.0 ? 1.85 : 1.05), sy / 0.72, sz / 0.74); float l = length(v); return vec4(v / l, 1.0 / l);
}
mat3 polyLock(float kind){ return kind < 2.5 ? rotX(0.62) * rotY(0.75) : kind < 3.5 ? rotX(0.45) * rotY(0.62) : rotZ(0.38) * rotX(0.55) * rotY(0.35); }
vec4 polyInner(vec3 q, float kind, vec3 hue, float t, float seed, float energy){
  vec3 light = mix(hue, vec3(1.0), 0.55), deep = hue * 0.35;
  vec3 w = vec3(fbm(q * 1.8 + seed * 7.0 + t * 0.06), fbm(q * 1.8 + 3.1 + t * 0.05), fbm(q * 1.8 + 5.2 - t * 0.04));
  float n = fbm(q * 2.2 + w * 1.4 + seed * 3.0);
  if (kind > 3.5) {                                   // 다리: 긴 축을 따라 빛이 흐르고 맥박 하나가 지나간다
    float f = exp(-(q.y * q.y + q.z * q.z) * 30.0);
    float pulse = exp(-pow(fract(t * 0.35 + seed) * 2.9 - 1.05 - q.x, 2.0) * 18.0) * f;
    return vec4((light * f * 1.3 + vec3(1.0) * pulse * 1.4 + mix(deep, light, n) * 0.5) * (1.0 + energy), f * 1.5 + pulse + smoothstep(0.5, 0.85, n) * 0.6);
  }
  float core = exp(-dot(q, q) * (kind < 2.5 ? 6.0 : 4.0));
  float dn = smoothstep(0.38, 0.78, n) * 0.9 + core * 0.8;
  vec3 c = mix(hue * 0.6, light, smoothstep(0.42, 0.78, n)) + light * core * 0.9;
  return vec4(c * (1.3 + energy * 1.5), dn);
}
vec4 polyOrb(vec3 d, vec3 c, float r, float kind, float state, vec3 hue, float energy, float t, float steps){
  mat3 M = polyLock(kind); vec3 O = M * (-c) / r, D = M * d / r; vec3 V = -d;
  int cnt = kind < 2.5 ? 4 : 8;
  float tE = -1e9, tX = 1e9; vec3 nE = vec3(0.0, 0.0, 1.0), nX = vec3(0.0, 0.0, 1.0);
  for (int i = 0; i < 8; i++) { if (i >= cnt) break; vec4 P = polyPlane(kind, i); float nd = dot(P.xyz, D); float no = P.w - dot(P.xyz, O);
    if (abs(nd) < 1e-9) { if (no < 0.0) tE = 1e9; continue; }
    float tt = no / nd; if (nd < 0.0) { if (tt > tE) { tE = tt; nE = P.xyz; } } else { if (tt < tX) { tX = tt; nX = P.xyz; } } }
  bool hit = tE < tX;
  vec4 outc = vec4(0.0);
  if (hit) {
    vec3 xE = O + D * tE, xX = O + D * tX;
    vec3 N = transpose(M) * nE; float ndv = clamp(dot(N, V), 0.0, 1.0);
    float sE = 1e9, sX = 1e9;
    for (int j = 0; j < 8; j++) { if (j >= cnt) break; vec4 P = polyPlane(kind, j);
      if (dot(P.xyz, nE) < 0.999) sE = min(sE, P.w - dot(P.xyz, xE));
      if (dot(P.xyz, nX) < 0.999) sX = min(sX, P.w - dot(P.xyz, xX)); }
    float wE = max(fwidth(sE) * 1.2, 0.012), wX = max(fwidth(sX) * 1.2, 0.012);
    float edgeF = 1.0 - smoothstep(0.0, wE, sE), edgeB = 1.0 - smoothstep(0.0, wX, sX);
    vec3 acc = vec3(0.0); float a = 0.0; float dt = (tX - tE) / max(steps, 1.0);
    for (int i = 0; i < 16; i++) { if (float(i) >= steps) break; vec3 q = O + D * (tE + (float(i) + 0.5) * dt);
      vec4 e = polyInner(q, kind, hue, t, vSeed, energy); float k = clamp(e.a * dt / r * 1.2, 0.0, 1.0); acc += (1.0 - a) * e.rgb * k; a += (1.0 - a) * k; }
    if (steps < 0.5) { acc = mix(hue, vec3(1.0), 0.55) * 0.55; a = 0.45; }   // 작을 때: 안의 빛을 한 톤으로
    vec3 body = glassBody(hue, ndv);
    float fres = pow(1.0 - ndv, 3.0);
    vec3 L = normalize(vec3(-0.45, 0.75, 0.55)), H = normalize(L + V);
    vec3 Ns = normalize(mix(N, normalize(transpose(M) * xE), 0.5));
    float spec = pow(max(dot(Ns, H), 0.0), 90.0) * 0.9 + pow(max(dot(N, H), 0.0), 24.0) * 0.10;
    float sheen = max(dot(N, L), 0.0);
    vec3 rimC = mix(hue, vec3(1.0), 0.35);
    float rimK = state > 1.5 && state < 2.5 ? 1.6 : 0.8;
    vec3 col = body * (1.0 - a) + acc + rimC * fres * rimK * 0.8 + hue * sheen * 0.10 + vec3(1.0) * spec
             + rimC * edgeF * (0.55 + 0.6 * rimK) + rimC * edgeB * 0.22;
    if (state > 2.5 && state < 3.5) col = desat(col, 0.85) * 0.6;
    outc = vec4(col, 1.0); solid = 1.0;
  }
  vec3 xc = O + D * dot(d, c); float sdf = -1e9;
  for (int j = 0; j < 8; j++) { if (j >= cnt) break; vec4 P = polyPlane(kind, j); sdf = max(sdf, dot(P.xyz, xc) - P.w); }
  float glowK = state < 0.5 ? 0.10 : state < 1.5 ? 0.55 : state < 2.5 ? 0.45 : state < 3.5 ? 0.0 : 0.6;
  if (!hit) { float g = exp(-max(sdf, 0.0) * 7.0) * glowK; outc.rgb += hue * g; outc.a = max(outc.a, g * 0.8); }
  if (!hit && state > 1.5 && state < 2.5) { float rk = kind < 2.5 ? 1.32 : kind < 3.5 ? 1.18 : 1.6; float cl = length(c - d * dot(d, c)) / r;
    float ring = 1.0 - smoothstep(0.0, 0.035, abs(cl - rk * 1.1)); outc.rgb = mix(outc.rgb, vec3(1.0), ring * 0.9); outc.a = max(outc.a, ring); }
  return outc;
}

void main(){
  vec3 d = normalize(vPos); vec3 c = vC; float r = vR;
  float kind = vKind, state = vState, t = uTime;
  vec3 hue = vHue;
  if (state > 3.5) hue = mix(vec3(1.0, 0.25, 0.3), vec3(1.0, 0.45, 0.35), 0.5 + 0.5 * sin(t * 4.0));   // 충돌: 붉게 맥동
  float energy = state > 0.5 && state < 2.5 ? 0.45 : 0.0;
  // 화면에서 작으면 레이마칭을 줄이거나 건너뛴다(점처럼 보이는 크기에서 안의 무늬는 안 읽힌다)
  float steps = vPx < 6.0 ? 0.0 : clamp(floor(vPx / 4.0), 4.0, uSteps);
  vec4 outc = vec4(0.0);
  if (kind > 1.5 && kind < 4.5) outc = polyOrb(d, vC, vR, kind, state, hue, energy, t, steps);
  else {
    float spin = t * (state > 0.5 && state < 2.5 ? 0.35 : 0.12) + vSeed * 6.283;
    mat3 R = rotX(0.35) * rotY(spin);
    float b = dot(d, c), cc = dot(c, c) - r * r, h = b * b - cc;
    float closest = length(c - d * b);
    bool hit = h > 0.0;
    if (hit) {
      float sh = sqrt(h); float t0 = b - sh, t1 = b + sh;
      vec3 P = d * t0, N = (P - c) / r, V = -d;
      float ndv = clamp(dot(N, V), 0.0, 1.0);
      float fres = pow(1.0 - ndv, 3.0);
      vec3 rimC = mix(hue, vec3(1.0), 0.35);
      if (kind > 4.5) {                                // 주제: 몸통 없이 앞뒤 점 껍질(크기로 깊이)
        vec3 N1 = (d * t1 - c) / r;
        float front = dotShell(R * N, 14.0, 0.075), back = dotShell(R * N1, 14.0, 0.05);
        vec3 dotC = mix(mix(hue, vec3(1.0), 0.5), hue * 0.85, uLight);
        outc = vec4(dotC * (front * (0.55 + 0.45 * ndv) + back * 0.28) + rimC * fres * 0.35,
                    clamp(front * 0.95 + back * 0.45 + fres * 0.25, 0.0, 1.0));
        if (state > 2.5 && state < 3.5) { outc.rgb = desat(outc.rgb, 0.85) * 0.6; outc.a *= 0.6; }
      } else {
        vec3 acc = vec3(0.0); float a = 0.0; float dt = (t1 - t0) / max(steps, 1.0);
        for (int i = 0; i < 16; i++) { if (float(i) >= steps) break;
          vec3 q = R * ((d * (t0 + (float(i) + 0.5) * dt) - c) / r);
          vec4 e = inner(q, kind, hue, t, vSeed, energy);
          float k = clamp(e.a * dt / r * 1.2, 0.0, 1.0);
          acc += (1.0 - a) * e.rgb * k; a += (1.0 - a) * k; }
        if (steps < 0.5) {
          acc = mix(hue, vec3(1.0), 0.55) * (kind < 0.5 ? 0.6 : 0.45) * (0.4 + 0.6 * ndv); a = 0.45;
          // 결정은 작아도 개념과 갈려야 한다: 안의 결정 대신 가운데 흰 점 하나
          if (kind > 0.5) { float cd = closest / r; acc += vec3(1.0, 0.97, 0.9) * (1.0 - smoothstep(0.22, 0.40, cd)); a = 0.6; }
        }
        vec3 L = normalize(vec3(-0.45, 0.75, 0.55)), H = normalize(L + V);
        float spec = pow(max(dot(N, H), 0.0), 90.0) * 0.9 + pow(max(dot(N, H), 0.0), 10.0) * 0.06;
        float under = pow(max(dot(N, normalize(vec3(0.3, -0.8, 0.4))), 0.0), 3.0) * 0.18;   // 안에서 튀어 오르는 빛
        float rimK = state > 1.5 && state < 2.5 ? 1.6 : 0.8;
        vec3 col = glassBody(hue, ndv) * (1.0 - a) + acc + rimC * fres * rimK + vec3(1.0) * spec + hue * under * (0.6 + energy);
        if (state > 2.5 && state < 3.5) col = desat(col, 0.85) * 0.6;   // 지난 것: 서리 낀 듯 물러난다
        outc = vec4(col, 1.0); solid = 1.0;
      }
    }
    // 유리 밖: 상태별 후광과 선택 고리
    float edge = (closest - r) / r;
    float glowK = state < 0.5 ? 0.10 : state < 1.5 ? 0.55 : state < 2.5 ? 0.45 : state < 3.5 ? 0.0 : 0.6;
    if (kind > 4.5) glowK *= 0.4;
    if (!hit) { float g = exp(-max(edge, 0.0) * 7.0) * glowK; outc.rgb += hue * g; outc.a = max(outc.a, g * 0.8); }
    if (!hit && state > 1.5 && state < 2.5) { float ring = 1.0 - smoothstep(0.0, 0.035, abs(edge - 0.2)); outc.rgb = mix(outc.rgb, vec3(1.0), ring * 0.9); outc.a = max(outc.a, ring); }
  }
  if (uPass < 0.5) { if (solid < 0.5) discard; gl_FragColor = vec4(outc.rgb, 1.0); return; }
  if (solid > 0.5 || outc.a < 0.003) discard;
  gl_FragColor = outc;
}`;

/** 노드 몸통 한 층. 인덱스는 호출측이 노드마다 정해 둔다(`set`). 자리·크기만 바뀌는
 *  매 프레임 갱신은 `move` 로 하고, 바뀐 것이 있으면 `flush` 가 버퍼를 올린다. */
export class OrbLayer {
  readonly body: THREE.Mesh;
  readonly halo: THREE.Mesh;
  private readonly geom: THREE.InstancedBufferGeometry;
  private readonly uniforms: Record<string, THREE.IUniform>;
  private readonly center: Float32Array;
  private readonly radius: Float32Array;
  private readonly kind: Float32Array;
  private readonly state: Float32Array;
  private readonly hue: Float32Array;
  private moved = false;
  private painted = false;

  constructor(count: number, light: boolean, steps = 12) {
    const g = new THREE.InstancedBufferGeometry();
    const plane = new THREE.PlaneGeometry(1, 1);
    g.index = plane.index; g.setAttribute("position", plane.attributes.position);
    g.instanceCount = count;
    const f = (n: number) => new Float32Array(Math.max(1, count) * n);
    this.center = f(3); this.radius = f(1); this.kind = f(1); this.state = f(1); this.hue = f(3);
    const seed = f(1);
    const ia = (a: Float32Array, n: number) => new THREE.InstancedBufferAttribute(a, n);
    g.setAttribute("aCenter", ia(this.center, 3)); g.setAttribute("aRadius", ia(this.radius, 1));
    g.setAttribute("aKind", ia(this.kind, 1)); g.setAttribute("aState", ia(this.state, 1));
    g.setAttribute("aSeed", ia(seed, 1)); g.setAttribute("aHue", ia(this.hue, 3));
    this.geom = g;
    this.uniforms = { uTime: { value: 0 }, uSteps: { value: steps }, uLight: { value: light ? 1 : 0 },
                      uPxUnit: { value: 500 } };
    const mat = (pass: number, transparent: boolean) => new THREE.ShaderMaterial({
      vertexShader: VERT, fragmentShader: FRAG, transparent, depthWrite: !transparent, depthTest: true,
      uniforms: { ...this.uniforms, uPass: { value: pass } },
    });
    this.body = new THREE.Mesh(g, mat(0, false));
    this.halo = new THREE.Mesh(g, mat(1, true));
    // 인스턴스가 씬 전체에 흩어져 있어 바운딩구가 의미 없다 — 각도에 따라 조용히 사라지지 않게 끈다.
    this.body.frustumCulled = false; this.halo.frustumCulled = false;
    this.halo.renderOrder = 1;            // 몸통이 깊이를 쓴 뒤에 그린다
  }

  /** 인스턴스 하나를 처음 놓는다. `seed` 는 안의 무늬가 노드마다 다르게 하는 값(0~1). */
  set(i: number, x: number, y: number, z: number, r: number, kind: number, state: number,
      seed: number, hue: THREE.Color): void {
    (this.geom.getAttribute("aSeed").array as Float32Array)[i] = seed;
    this.kind[i] = kind; this.state[i] = state;
    this.move(i, x, y, z, r);
    this.tint(i, hue);
    this.painted = true;
  }
  /** 자리와 반경(종 보정 전). 반경 0 이면 그리지 않는다(시간 커서가 숨긴 노드). */
  move(i: number, x: number, y: number, z: number, r: number): void {
    this.center[i * 3] = x; this.center[i * 3 + 1] = y; this.center[i * 3 + 2] = z;
    this.radius[i] = r * (KIND_SCALE[this.kind[i]] ?? 1);
    this.moved = true;
  }
  tint(i: number, hue: THREE.Color): void {
    this.hue[i * 3] = hue.r; this.hue[i * 3 + 1] = hue.g; this.hue[i * 3 + 2] = hue.b;
    this.painted = true;
  }
  /** 그리기 전에 한 번 — 바뀐 버퍼만 올리고 시간·화면 배율을 넘긴다. */
  flush(time: number, pxPerUnit: number): void {
    if (this.moved) {
      this.geom.getAttribute("aCenter").needsUpdate = true;
      this.geom.getAttribute("aRadius").needsUpdate = true;
      this.moved = false;
    }
    if (this.painted) {
      for (const k of ["aHue", "aKind", "aState", "aSeed"]) this.geom.getAttribute(k).needsUpdate = true;
      this.painted = false;
    }
    this.uniforms.uTime.value = time;
    this.uniforms.uPxUnit.value = pxPerUnit;
  }
  setLight(light: boolean): void { this.uniforms.uLight.value = light ? 1 : 0; }
  /** QA 용 — 인스턴스의 지금 반경(시간 커서가 숨기면 0). */
  radiusAt(i: number): number { return this.radius[i]; }
  get count(): number { return this.geom.instanceCount; }
  dispose(): void {
    this.geom.dispose();
    (this.body.material as THREE.Material).dispose();
    (this.halo.material as THREE.Material).dispose();
  }
}

/** 노드 id → 0~1 씨앗. 같은 노드는 언제 그려도 같은 무늬다(D17 결정론과 같은 규율). */
export function orbSeed(id: string): number {
  let h = 2166136261;
  for (let i = 0; i < id.length; i++) { h ^= id.charCodeAt(i); h = Math.imul(h, 16777619); }
  return (h >>> 0) / 4294967296;
}

/** 영역 = 빛나는 유리 부피 하나(점 껍질 없이 부피만). 영역 안의 주제는 같은
 *  시간 축(x)을 나눠 쓰므로 영역은 x 를 따라 긴 관이다. 그래서 구가 아니라 **x 로 늘인
 *  타원체**다 — 구로 감싸면 시간 폭만큼 부풀어 이웃 영역을 덮는다.
 *  가장자리만 짙고 속은 비어 보이게 뒷면에 프레넬을 칠한다(앞면을 칠하면 안의 노드를 가린다).
 *  근거: [[vinc.research.graph-layout-direction-rules-2026-10-28]] §7 */
export type AreaBox = { cx: number; cy: number; cz: number; hx: number; rc: number };
export function areaBox(pts: { x: number; y: number; z: number; r: number }[]): AreaBox | null {
  if (pts.length < 2) return null;
  let x0 = Infinity, x1 = -Infinity, cy = 0, cz = 0;
  for (const p of pts) { x0 = Math.min(x0, p.x - p.r); x1 = Math.max(x1, p.x + p.r); cy += p.y; cz += p.z; }
  cy /= pts.length; cz /= pts.length;
  let rc = 0;
  for (const p of pts) rc = Math.max(rc, Math.hypot(p.y - cy, p.z - cz) + p.r);
  // 끝의 노드가 껍질에 닿지 않게 조금 띄운다(타원체는 끝으로 갈수록 가늘어진다)
  return { cx: (x0 + x1) / 2, cy, cz, hx: Math.max((x1 - x0) / 2 * 1.1, rc), rc: rc * 1.18 };
}
const AREA_GEOM = new THREE.SphereGeometry(1, 48, 32);
export function areaVolume(box: AreaBox, color: THREE.Color, light: boolean): THREE.Mesh {
  const m = new THREE.Mesh(AREA_GEOM, new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, side: THREE.BackSide,
    blending: light ? THREE.NormalBlending : THREE.AdditiveBlending,
    uniforms: { col: { value: color.clone() }, k: { value: light ? .2 : .8 } },
    vertexShader: /* glsl */`varying vec3 vN; varying vec3 vV;
      void main(){ vN = normalize(normalMatrix * normal); vec4 mv = modelViewMatrix * vec4(position, 1.0);
        vV = normalize(-mv.xyz); gl_Position = projectionMatrix * mv; }`,
    fragmentShader: /* glsl */`uniform vec3 col; uniform float k; varying vec3 vN; varying vec3 vV;
      void main(){ float f = pow(1.0 - abs(dot(vN, vV)), 3.4); gl_FragColor = vec4(col, f * k); }`,
  }));
  m.position.set(box.cx, box.cy, box.cz);
  m.scale.set(box.hx, box.rc, box.rc);
  m.renderOrder = -1;                     // 노드 후광보다 먼저(뒤에 깔린다)
  return m;
}
export function retintVolume(m: THREE.Mesh, color: THREE.Color, light: boolean): void {
  const mat = m.material as THREE.ShaderMaterial;
  mat.uniforms.col.value.copy(color);
  mat.uniforms.k.value = light ? .2 : .8;
  mat.blending = light ? THREE.NormalBlending : THREE.AdditiveBlending;
  mat.needsUpdate = true;
}
