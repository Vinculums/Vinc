/** 나침반 손 확인 판(compass-whole.html)과 측정 스크립트가 쓰는 결정적 합성 그래프. CI 는 돌리지 않는다.
 *
 *  허브가 주는 모양(GraphNode · GraphEdge: id · kind · title · label · domain · ts · content_ts · degree)으로 만든다.
 *  주제는 세 단(영역 → 둘째 → 셋째)이고 크기는 한쪽으로 쏠린다: 몇 주제는 150~300 노드, 대부분은 10 아래.
 *  다리는 주제가 없고(domain 빈 값) CONNECTS 로 다른 영역의 개념들을 잇는다. 같은 seed 면 늘 같은 그래프다. */
export interface SynthOpts { nodes?: number; topics?: number; seed?: number; lang?: "en" | "ko" }
export interface SynthNode { id: string; kind: string; title: string; label: string; domain: string; ts: string; content_ts?: string; valid_from?: string; degree: number }
export interface SynthEdge { source: string; target: string; type: string }

const EN = ("quantum field theory gauge symmetry entropy lattice spectral category functor topology manifold kernel " +
  "market pricing auction contract labour capital welfare policy growth inflation interest credit liquidity " +
  "neuron synapse cortex memory learning signal protein enzyme genome cell immune virus vaccine climate carbon ocean " +
  "river glacier soil forest species habitat orbit planet stellar galaxy plasma fusion laser photon optics crystal " +
  "polymer alloy battery circuit compiler parser runtime cache index query schema graph tree heap queue stream " +
  "protocol network router packet cipher proof lemma theorem axiom logic set measure integral series sequence " +
  "poetry novel rhythm metre canon archive museum painting sculpture harmony melody chord opera film theatre ritual " +
  "language grammar syntax semantics dialect migration city empire treaty revolution reform law court justice").split(" ");
const KO = ("양자 장 이론 게이지 대칭 엔트로피 격자 스펙트럼 범주 함자 위상 다양체 커널 시장 가격 경매 계약 노동 자본 " +
  "복지 정책 성장 물가 금리 신용 유동성 뉴런 시냅스 피질 기억 학습 신호 단백질 효소 유전체 세포 면역 바이러스 백신 " +
  "기후 탄소 해양 하천 빙하 토양 숲 생물종 서식지 궤도 행성 항성 은하 플라스마 핵융합 레이저 광자 광학 결정 고분자 " +
  "합금 배터리 회로 컴파일러 구문분석 실행 캐시 색인 질의 스키마 그래프 나무 힙 대기열 흐름 규약 연결망 패킷 암호 " +
  "증명 보조정리 정리 공리 논리 집합 측도 적분 급수 수열 시 소설 운율 정전 기록보관 박물관 회화 조각 화성 선율 " +
  "화음 오페라 영화 연극 의례 언어 문법 통사 의미론 방언 이주 도시 제국 조약 혁명 개혁 법 법원 정의").split(" ");

/** mulberry32: 같은 seed 면 같은 수열 */
function rng(seed: number) { let a = seed >>> 0; return () => { a = (a + 0x6d2b79f5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }

export function synthGraph(o: SynthOpts = {}): { nodes: SynthNode[]; edges: SynthEdge[] } {
  const N = Math.max(40, o.nodes ?? 2500), T = Math.max(6, o.topics ?? 170), R = rng(o.seed ?? 7), W = o.lang === "ko" ? KO : EN;
  const pick = <X>(a: X[]) => a[Math.floor(R() * a.length)];
  const words = (n: number) => Array.from({ length: n }, () => pick(W)).join(" ");
  const cap = (s: string) => s.replace(/(^|\s)\S/g, (c) => c.toUpperCase());
  const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9가-힣]+/g, "-").replace(/^-|-$/g, "");
  // 주제 나무: 영역 약 7%, 둘째 단 약 30%, 나머지는 셋째 단. 이름은 단마다 겹치지 않게 번호를 붙인다
  const nA = Math.max(2, Math.round(T * .07)), n2 = Math.max(2, Math.round(T * .3)), n3 = Math.max(0, T - nA - n2);
  const areas = Array.from({ length: nA }, (_, i) => `${slug(pick(W))}${i}`);
  const lvl2 = Array.from({ length: n2 }, (_, i) => `${areas[Math.floor(Math.pow(R(), 1.6) * nA)]}/${slug(pick(W))}${i}`);
  const lvl3 = Array.from({ length: n3 }, (_, i) => `${lvl2[Math.floor(Math.pow(R(), 1.4) * n2)]}/${slug(pick(W))}${i}`);
  const topics = [...areas, ...lvl2, ...lvl3];
  // 크기: 앞의 넷은 큰 주제(300 · 240 · 190 · 150, 그래프가 작으면 비율로), 나머지는 지프 꼬리. 모든 주제에 적어도 하나
  const nBridge = Math.max(2, Math.round(N * .024)), body = N - nBridge, order = topics.map((t, i) => [t, R() + (i < nA ? 0 : .3)] as const).sort((a, b) => a[1] - b[1]).map(([t]) => t);
  const big = [300, 240, 190, 150].map((x) => Math.round(x * body / 2440)), size = new Map<string, number>();
  order.forEach((t, i) => size.set(t, i < big.length ? big[i] : 1));
  let left = body - [...size.values()].reduce((s, x) => s + x, 0);
  const tail = order.slice(big.length), wsum = tail.reduce((s, _, i) => s + 1 / Math.pow(i + 1, 1.15), 0);
  tail.forEach((t, i) => { const add = Math.floor(left * (1 / Math.pow(i + 1, 1.15)) / wsum); size.set(t, size.get(t)! + add); });
  left = body - [...size.values()].reduce((s, x) => s + x, 0);
  for (let i = 0; left > 0; i++, left--) size.set(tail[i % tail.length], size.get(tail[i % tail.length])! + 1);

  const day0 = Date.parse("2025-11-12"), dayN = Date.parse("2026-11-11");
  const date = () => new Date(day0 + Math.floor(R() * (dayN - day0) / 864e5) * 864e5).toISOString().slice(0, 10);
  const nodes: SynthNode[] = [], edges: SynthEdge[] = [], byTopic = new Map<string, SynthNode[]>(), seen = new Set<string>();
  const uid = (base: string) => { let id = base, k = 2; while (seen.has(id)) id = `${base}-${k++}`; seen.add(id); return id; };
  for (const t of topics) {
    const list: SynthNode[] = []; byTopic.set(t, list);
    for (let i = 0; i < size.get(t)!; i++) {
      const u = R(), kind = u < .5 ? "concept" : u < .56 ? "concept-doc" : u < .68 ? "decision" : u < .86 ? "record" : "episode";
      const nw = kind === "concept" || kind === "concept-doc" ? 1 + Math.floor(Math.pow(R(), 2) * 4) : 3 + Math.floor(R() * 6);
      const title = cap(words(nw)), d = date();
      const id = kind === "concept-doc" ? uid(`concept:doc.${slug(title)}`) : uid(`${kind}:${slug(title)}`);
      const n: SynthNode = { id, kind: kind === "concept-doc" ? "concept" : kind, title, label: title, domain: t, ts: d, degree: 0 };
      if (R() < .7) n.content_ts = d;
      if (kind === "decision") n.valid_from = d;
      list.push(n); nodes.push(n);
    }
  }
  const mean = (l: SynthNode[]) => l.filter((n) => n.kind === "concept");
  const parentOf = (t: string) => t.split("/").slice(0, -1).join("/");
  const edge = (a: SynthNode, b: SynthNode, type: string) => { if (a === b) return; edges.push({ source: a.id, target: b.id, type }); a.degree++; b.degree++; };
  for (const t of topics) {
    const l = byTopic.get(t)!, m = mean(l), up = mean(byTopic.get(parentOf(t)) || []);
    for (const n of l) {
      if (n.kind === "concept") {
        // 뜻의 관계: 주로 같은 주제 안, 넷 중 하나는 다른 곳(형제 · 아무 데나)
        const u = R(), k = u < .3 ? 1 : u < .75 ? 2 : 3;
        for (let j = 0; j < k; j++) { const far = R() < .25, pool = far ? (R() < .5 ? mean(byTopic.get(pick(topics))!) : up) : m; if (pool.length) edge(n, pick(pool), "RELATED_TO"); }
        if (R() < .3 && m.length > 1) edge(n, pick(m), pick(["PREREQUISITE_OF", "DERIVES_FROM", "INHERITS_FROM"]));
      } else {
        // 붙은 것: 같은 주제의 개념(없으면 위 주제의 개념)에 붙는다
        const host = m.length ? pick(m) : up.length ? pick(up) : null;
        if (host) edge(n, host, n.kind === "episode" ? "ABOUT" : "ATTACHES_TO");
      }
    }
  }
  // 다리: 다른 영역의 개념 둘이나 셋을 잇는다(셋째 끝은 같은 영역일 수도 있다)
  const allMean = mean(nodes);
  for (let i = 0; i < nBridge; i++) {
    const a = pick(allMean), b = pick(allMean.filter((x) => x.domain.split("/")[0] !== a.domain.split("/")[0])) || pick(allMean);
    const title = `${cap(words(1))} ↔ ${cap(words(1))}`, br: SynthNode = { id: uid(`bridge:${slug(title)}`), kind: "bridge", title, label: title, domain: "", ts: date(), degree: 0 };
    nodes.push(br); edge(br, a, "CONNECTS"); edge(br, b, "CONNECTS"); if (R() < .3) edge(br, pick(allMean), "CONNECTS");
  }
  return { nodes, edges };
}
