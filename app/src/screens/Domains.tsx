import { useEffect } from "react";
import { useApp } from "../lib/app";
import { useAsync } from "../lib/useAsync";
import type { DomainStat } from "../lib/api";
import {
  Button,
  EmptyGuide,
  ErrorBanner,
  Skeleton,
} from "../components/primitives";

// 화면 5. 데이터셋 분포 (/domains) — 도메인 분포 바. 4-State.
export function DomainsScreen() {
  const { client } = useApp();
  const dom = useAsync<DomainStat[]>(() => client.domains());

  useEffect(() => {
    void dom.run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const max = Math.max(1, ...(dom.data ?? []).map((d) => d.count));

  return (
    <div className="section">
      {dom.state === "loading" && (
        <div className="panel stack" style={{ padding: "var(--space-4)" }}>
          {[70, 50, 40, 25].map((w, i) => (
            <Skeleton key={i} height={16} width={`${w}%`} />
          ))}
        </div>
      )}

      {dom.state === "error" && (
        <ErrorBanner
          message="분포 로드 실패 — 다시 시도"
          detail={dom.error ?? undefined}
          onRetry={() => dom.run()}
        />
      )}

      {dom.state === "success" &&
        (dom.data && dom.data.length > 0 ? (
          <div className="panel" style={{ padding: "var(--space-5)" }}>
            {dom.data.map((d) => (
              <div className="dombar" key={d.domain}>
                <span style={{ fontWeight: 600 }}>{d.domain}</span>
                <span className="dombar__track">
                  <span
                    className="dombar__fill"
                    style={{ width: `${Math.round((d.count / max) * 100)}%` }}
                  />
                </span>
                <span className="muted mono" style={{ textAlign: "right" }}>
                  {d.count}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <div className="panel">
            <EmptyGuide
              icon="domains"
              title="도메인 없음 — 적재로 시작"
              hint="레코드가 쌓이면 도메인 분포가 계산됩니다."
              action={<Button variant="primary" icon="ingest">적재로 이동</Button>}
            />
          </div>
        ))}
    </div>
  );
}
