import { useState } from "react";
import { useApp } from "../lib/app";
import { useAsync } from "../lib/useAsync";
import type { SearchHit } from "../lib/api";
import { Icon } from "../components/Icon";
import {
  Button,
  EmptyGuide,
  ErrorBanner,
  ListRow,
  Skeleton,
} from "../components/primitives";

// 화면 7. 검색 (/search) — 의미·키워드 하이브리드(RRF). 4-State.
export function SearchScreen() {
  const { client } = useApp();
  const [query, setQuery] = useState("");
  const search = useAsync<SearchHit[]>((q: string) => client.search(q, 8));

  return (
    <div className="section">
      <form
        className="row"
        onSubmit={(e) => {
          e.preventDefault();
          if (query.trim()) void search.run(query);
        }}
      >
        <input
          className="input"
          style={{ flex: 1 }}
          autoFocus
          placeholder="의미·키워드 검색 — 예: 환불 정책"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <Button type="submit" variant="primary" icon="search" disabled={!query.trim()}>
          검색
        </Button>
      </form>

      {search.state === "idle" && (
        <div className="panel">
          <EmptyGuide
            icon="search"
            title="검색어를 입력하세요"
            hint="벡터(HNSW) + BM25 를 RRF 로 융합해 상위 결과를 냅니다."
          />
        </div>
      )}

      {search.state === "loading" && (
        <div className="panel stack" style={{ padding: "var(--space-4)" }}>
          <Skeleton height={22} width="70%" />
          <Skeleton height={22} width="55%" />
          <Skeleton height={22} width="62%" />
        </div>
      )}

      {search.state === "error" && (
        <ErrorBanner
          message="검색 실패 — 다시 시도"
          detail={search.error ?? undefined}
          onRetry={() => search.run(query)}
        />
      )}

      {search.state === "success" &&
        (search.data && search.data.length > 0 ? (
          <div className="panel hairline-list">
            {search.data.map((h) => (
              <ListRow
                key={h.id}
                leading={<Icon name="disc" size={12} />}
                title={h.title}
                meta={h.snippet}
                trailing={<span className="mono muted">{h.score.toFixed(2)}</span>}
              />
            ))}
          </div>
        ) : (
          <div className="panel">
            <EmptyGuide
              icon="ring"
              title={`"${query}" 결과 없음`}
              hint="다른 표현으로 검색하거나 소스를 더 적재하세요."
            />
          </div>
        ))}
    </div>
  );
}
