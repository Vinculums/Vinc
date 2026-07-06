import { useState } from "react";
import { useApp } from "../lib/app";
import { useAsync } from "../lib/useAsync";
import { pickDirectory } from "../lib/ipc";
import { Icon } from "../components/Icon";
import {
  Button,
  EmptyGuide,
  ErrorBanner,
  Progress,
} from "../components/primitives";

// 화면 8. 팩 내보내기 (/activate) — 산출물 팩 내보내기. 4-State.
export function PackScreen() {
  const { client, session } = useApp();
  const [query, setQuery] = useState("");
  const [out, setOut] = useState<string>("");
  const pack = useAsync<{ pack?: string; count?: number }>((o?: string) =>
    client.activate(session.dataDir || "owner", query, o),
  );

  async function chooseOut() {
    const dir = await pickDirectory("팩 출력 폴더 선택");
    if (dir) setOut(dir);
  }

  return (
    <div className="section">
      <div className="stack" style={{ maxWidth: "42rem" }}>
        <div className="field">
          <label>스코프 질의(선택)</label>
          <input
            className="input"
            placeholder="비우면 전체 · 예: billing"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <div className="field">
          <label>출력 폴더</label>
          <div className="picker">
            <span className="picker__path">{out || "기본 packs/ 폴더"}</span>
            <Button variant="ghost" size="sm" icon="download" onClick={chooseOut}>
              폴더 선택
            </Button>
          </div>
        </div>
      </div>

      {pack.state === "idle" && (
        <div className="panel">
          <EmptyGuide
            icon="pack-export"
            title="팩을 아직 내보내지 않음"
            hint="선택 스코프의 컨텍스트를 이식 가능한 팩(+MCP manifest)으로 내보냅니다."
            action={
              <Button variant="primary" icon="pack-export" onClick={() => pack.run(out)}>
                팩 내보내기
              </Button>
            }
          />
        </div>
      )}

      {pack.state === "loading" && (
        <div className="stack">
          <Progress />
          <span className="muted">팩 생성 중 — 오프라인</span>
        </div>
      )}

      {pack.state === "success" && (
        <div className="panel stack" style={{ padding: "var(--space-5)" }}>
          <span className="row" style={{ gap: "var(--space-2)" }}>
            <Icon name="check" size={20} />
            <strong>내보내기 완료</strong>
          </span>
          <span className="muted mono">{pack.data?.pack}</span>
          <span className="muted">{pack.data?.count ?? 0}개 레코드 · packs/ 저장(오프라인)</span>
          <div className="row">
            <Button variant="ghost" icon="undo" onClick={() => pack.reset()}>
              다시 내보내기
            </Button>
          </div>
        </div>
      )}

      {pack.state === "error" && (
        <ErrorBanner
          message="내보내기 실패 — 다시 시도"
          detail={pack.error ?? undefined}
          onRetry={() => pack.run(out)}
        />
      )}
    </div>
  );
}
