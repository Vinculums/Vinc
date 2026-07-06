import { useEffect, useState } from "react";
import { useApp } from "../lib/app";
import { useAsync } from "../lib/useAsync";
import type { RecallRecord } from "../lib/api";
import { Icon } from "../components/Icon";
import {
  Button,
  EmptyGuide,
  ErrorBanner,
  ListRow,
  Skeleton,
  StatusMark,
} from "../components/primitives";

// 화면 4. 레코드 탐색 (/recall) — 열람·질의. 4-State.
export function RecallScreen() {
  const { client } = useApp();
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const recall = useAsync<RecallRecord[]>((q: string) => client.recall(q));

  // 진입 시 1회 로드(idle→loading→success).
  useEffect(() => {
    void recall.run("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="section">
      <form
        className="row"
        onSubmit={(e) => {
          e.preventDefault();
          void recall.run(query);
        }}
      >
        <input
          className="input"
          style={{ flex: 1 }}
          placeholder="레코드 검색 — 제목·도메인"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <Button type="submit" variant="ghost" icon="search">
          조회
        </Button>
      </form>

      {recall.state === "loading" && (
        <div className="panel stack" style={{ padding: "var(--space-4)" }}>
          <Skeleton height={20} width="60%" />
          <Skeleton height={20} width="80%" />
          <Skeleton height={20} width="45%" />
        </div>
      )}

      {recall.state === "error" && (
        <ErrorBanner
          message="레코드 로드 실패 — 다시 시도"
          detail={recall.error ?? undefined}
          onRetry={() => recall.run(query)}
        />
      )}

      {recall.state === "success" &&
        (recall.data && recall.data.length > 0 ? (
          <div className="panel hairline-list">
            {recall.data.map((r) => (
              <ListRow
                key={r.id}
                selected={selected === r.id}
                onClick={() => setSelected(r.id)}
                leading={
                  <Icon name={r.pinned ? "pin" : "dot"} size={16} />
                }
                title={r.title}
                meta={
                  <span className="row" style={{ gap: "var(--space-2)" }}>
                    {r.domain && <span>{r.domain}</span>}
                    {r.ts && <span>· {r.ts}</span>}
                    {r.tags?.map((t) => (
                      <span key={t} className="mono">#{t}</span>
                    ))}
                  </span>
                }
                trailing={
                  <Button
                    size="sm"
                    variant="ghost"
                    icon="pin"
                    onClick={() => client.pin(r.id, r.pinned)}
                  />
                }
              />
            ))}
          </div>
        ) : (
          <div className="panel">
            <EmptyGuide
              icon="recall"
              title="레코드 없음 — 적재로 시작"
              hint="소스를 적재하면 레코드가 여기에 나타납니다."
              action={<Button variant="primary" icon="ingest">적재로 이동</Button>}
            />
          </div>
        ))}

      {recall.state === "success" && (
        <StatusMark state="success" label={`${recall.data?.length ?? 0}건`} />
      )}
    </div>
  );
}
