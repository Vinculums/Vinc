import { useState } from "react";
import { useApp } from "../lib/app";
import { useAsync } from "../lib/useAsync";
import { pickFiles } from "../lib/ipc";
import { Icon } from "../components/Icon";
import {
  Button,
  EmptyGuide,
  ErrorBanner,
  ListRow,
  Progress,
  Tabs,
} from "../components/primitives";

// 화면 3. 적재 ([[vinc.d.ingest]]) — 소스 → 레코드. 4-State.
const ENGINES = [
  { value: "recall", label: "Recall" },
  { value: "chunk", label: "Chunk" },
  { value: "decision", label: "Decision" },
];

export function IngestScreen() {
  const { client } = useApp();
  const [engine, setEngine] = useState("recall");
  const [sources, setSources] = useState<string[]>([]);
  const job = useAsync(async (eng: string, files: string[]) =>
    client.ingest(eng, { sources: files, episodes: [] }),
  );

  async function chooseSources() {
    const files = await pickFiles("적재할 소스 선택");
    if (files.length) setSources((s) => [...new Set([...s, ...files])]);
  }

  return (
    <div className="section">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <Tabs items={ENGINES} value={engine} onChange={setEngine} />
        <Button variant="ghost" size="sm" icon="upload" onClick={chooseSources}>
          소스 선택
        </Button>
      </div>

      {/* idle */}
      {job.state === "idle" && sources.length === 0 && (
        <div className="panel">
          <EmptyGuide
            icon="ingest"
            title="적재된 소스 없음"
            hint="파일을 선택하거나 이 영역에 드롭하세요."
            action={
              <Button variant="primary" icon="upload" onClick={chooseSources}>
                소스 선택
              </Button>
            }
          />
        </div>
      )}

      {/* 선택된 소스 목록 (idle, 준비) */}
      {sources.length > 0 && job.state !== "loading" && (
        <div className="stack">
          <div className="panel hairline-list">
            {sources.map((s) => (
              <ListRow
                key={s}
                leading={<Icon name="dot" size={14} />}
                title={s.split("/").pop()}
                meta={s}
                trailing={
                  <Button
                    size="sm"
                    variant="ghost"
                    icon="x"
                    onClick={() => setSources((v) => v.filter((x) => x !== s))}
                  />
                }
              />
            ))}
          </div>
          <div className="row">
            <Button
              variant="primary"
              icon="play"
              onClick={() => job.run(engine, sources)}
            >
              적재 실행 · {engine}
            </Button>
          </div>
        </div>
      )}

      {/* loading */}
      {job.state === "loading" && (
        <div className="stack">
          <Progress />
          <span className="muted">적재 중 · {engine} 엔진 — 소스 {sources.length}건</span>
        </div>
      )}

      {/* success */}
      {job.state === "success" && (
        <div className="panel">
          <div style={{ padding: "var(--space-4)" }} className="stack">
            <strong>적재 완료</strong>
            <span className="muted mono">{JSON.stringify(job.data?.result ?? job.data)}</span>
            <div className="row">
              <Button variant="ghost" icon="recall">레코드 탐색으로</Button>
              <Button variant="ghost" icon="undo" onClick={() => { job.reset(); setSources([]); }}>
                새 적재
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* error */}
      {job.state === "error" && (
        <ErrorBanner
          message="적재 실패 — 소스 확인 후 재시도"
          detail={job.error ?? undefined}
          onRetry={() => job.run(engine, sources)}
        />
      )}
    </div>
  );
}
