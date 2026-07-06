import { useEffect, useState } from "react";
import { useApp } from "../lib/app";
import { useAsync } from "../lib/useAsync";
import type { GraphData } from "../lib/api";
import { GraphView } from "../components/GraphView";
import { Icon } from "../components/Icon";
import {
  Button,
  EmptyGuide,
  ErrorBanner,
  StatusMark,
} from "../components/primitives";

// 화면 6. 그래프 마이닝 ([[vinc.d.graph]]) — Sigma.js/WebGL. 좌 필터·중앙 캔버스·우 상세/결정.
export function GraphScreen() {
  const { client, session } = useApp();
  const [domain, setDomain] = useState<string>("");
  const [selected, setSelected] = useState<string | null>(null);
  const graph = useAsync<GraphData>((d?: string) =>
    client.graph(session.dataDir || "owner", d || undefined),
  );

  useEffect(() => {
    void graph.run("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const node = graph.data?.nodes.find((n) => n.id === selected) ?? null;

  return (
    <div className="graphview">
      {/* 좌: 필터·질의 */}
      <aside className="graphview__side">
        <strong>필터</strong>
        <div className="field">
          <label>도메인</label>
          <input
            className="input"
            placeholder="전체"
            value={domain}
            onChange={(e) => setDomain(e.target.value)}
          />
        </div>
        <Button variant="ghost" icon="sync" onClick={() => graph.run(domain)}>
          그래프 갱신
        </Button>
        <StatusMark
          state={graph.state}
          label={
            graph.state === "success"
              ? `${graph.data?.nodes.length ?? 0} 노드`
              : graph.state === "loading"
                ? "불러오는 중"
                : graph.state === "error"
                  ? "오류"
                  : "대기"
          }
        />
      </aside>

      {/* 중앙: Sigma 캔버스 */}
      <div className="graphview__canvas">
        {graph.state === "loading" && (
          <div className="empty">
            <span className="spin">
              <Icon name="sync" size={28} />
            </span>
            <span className="muted">그래프 불러오는 중</span>
          </div>
        )}
        {graph.state === "error" && (
          <div style={{ padding: "var(--space-4)" }}>
            <ErrorBanner
              message="그래프 로드 실패 — 다시 시도"
              detail={graph.error ?? undefined}
              onRetry={() => graph.run(domain)}
            />
          </div>
        )}
        {graph.state === "idle" && (
          <EmptyGuide
            icon="graph"
            title="그래프 없음 — 적재/검색으로 노드 생성"
            hint="레코드가 생기면 노드·엣지가 그려집니다."
          />
        )}
        {graph.state === "success" &&
          (graph.data && graph.data.nodes.length > 0 ? (
            <GraphView data={graph.data} selected={selected} onSelect={setSelected} />
          ) : (
            <EmptyGuide icon="graph" title="노드 없음" hint="검색으로 이동해 노드를 만드세요." />
          ))}
      </div>

      {/* 우: 선택 노드 상세 · 결정 */}
      <aside className="graphview__side">
        <strong>선택 노드</strong>
        {node ? (
          <div className="panel stack" style={{ padding: "var(--space-4)" }}>
            <span className="row" style={{ gap: "var(--space-2)" }}>
              <Icon
                name={node.state === "full" ? "disc" : node.state === "half" ? "half-disc" : "ring"}
                size={16}
              />
              <strong>{node.label}</strong>
            </span>
            {node.domain && <span className="muted">도메인 · {node.domain}</span>}
            {/* primary CTA 1개 — 결정 실행 */}
            <Button
              variant="primary"
              icon="decide"
              onClick={() => client.decide(node.label, 2)}
            >
              결정 실행
            </Button>
            <div className="row">
              <Button size="sm" variant="ghost" icon="pin" onClick={() => client.pin(node.id)}>
                핀
              </Button>
              <Button size="sm" variant="ghost" icon="tag" onClick={() => client.tag(node.id, ["mined"])}>
                태그
              </Button>
            </div>
          </div>
        ) : (
          <span className="muted">캔버스에서 노드를 선택하세요.</span>
        )}
      </aside>
    </div>
  );
}
