import { useEffect, useRef } from "react";
import Graph from "graphology";
import Sigma from "sigma";
import forceAtlas2 from "graphology-layout-forceatlas2";
import type { GraphData } from "../lib/api";

// Sigma.js/WebGL 그래프 래퍼([[vinc.d.graph]]) — 대규모 노드 대비 전용 렌더러.
// 색은 모노크롬(잉크 톤). 상태 3단계는 색 hue 가 아니라 밝기/크기로 변별.
// 강조는 --ring 아웃라인 1개(선택 노드)만.

// 런타임에 CSS 토큰(HSL 채널)을 읽어 Sigma 가 쓰는 구체 색 문자열로 변환.
function tokenColor(name: string, alpha = 1): string {
  const raw = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  // tokens.css 는 "H S% L%" 채널만 담는다.
  return alpha === 1 ? `hsl(${raw})` : `hsl(${raw} / ${alpha})`;
}

const STATE_ALPHA: Record<string, number> = { full: 1, half: 0.55, empty: 0.28 };
const STATE_SIZE: Record<string, number> = { full: 12, half: 9, empty: 7 };

export function GraphView({
  data,
  selected,
  onSelect,
}: {
  data: GraphData;
  selected: string | null;
  onSelect: (id: string | null) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const sigmaRef = useRef<Sigma | null>(null);
  const graphRef = useRef<Graph | null>(null);

  // 그래프 구성 + 레이아웃 + Sigma 인스턴스(데이터 변경 시 재구성).
  useEffect(() => {
    if (!containerRef.current) return;
    const ink = tokenColor("--foreground");
    const hairline = tokenColor("--muted-foreground", 0.4);

    const graph = new Graph();
    data.nodes.forEach((n, i) => {
      const angle = (2 * Math.PI * i) / Math.max(1, data.nodes.length);
      graph.addNode(n.id, {
        label: n.label,
        x: Math.cos(angle),
        y: Math.sin(angle),
        size: STATE_SIZE[n.state ?? "full"] ?? 10,
        color: tokenColor("--foreground", STATE_ALPHA[n.state ?? "full"] ?? 1),
        // 원 렌더러(모노크롬). hexagon 표기는 후속 커스텀 프로그램으로 교체 가능.
      });
    });
    data.edges.forEach((e, i) => {
      if (graph.hasNode(e.source) && graph.hasNode(e.target)) {
        graph.addEdgeWithKey(`e${i}`, e.source, e.target, {
          color: hairline,
          size: 1.2,
        });
      }
    });

    // 힘기반 레이아웃(결정론적 iteration 수 — 랜덤 시드 없음).
    if (graph.order > 0) {
      forceAtlas2.assign(graph, { iterations: 120, settings: { gravity: 1, scalingRatio: 8 } });
    }

    const renderer = new Sigma(graph, containerRef.current, {
      renderEdgeLabels: false,
      defaultEdgeColor: hairline,
      labelColor: { color: ink },
      labelFont: "Satoshi, ui-sans-serif, system-ui",
      labelSize: 12,
    });

    renderer.on("clickNode", ({ node }) => onSelect(node));
    renderer.on("clickStage", () => onSelect(null));

    sigmaRef.current = renderer;
    graphRef.current = graph;
    return () => {
      renderer.kill();
      sigmaRef.current = null;
      graphRef.current = null;
    };
  }, [data, onSelect]);

  // 선택 노드 강조 — --ring 아웃라인 1개(highlighted).
  useEffect(() => {
    const graph = graphRef.current;
    const renderer = sigmaRef.current;
    if (!graph || !renderer) return;
    graph.forEachNode((id) => {
      graph.setNodeAttribute(id, "highlighted", id === selected);
    });
    renderer.refresh();
  }, [selected]);

  return <div ref={containerRef} style={{ position: "absolute", inset: 0 }} />;
}
