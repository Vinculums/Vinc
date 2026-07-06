import { useState } from "react";
import { Icon } from "../components/Icon";
import { Button, ErrorBanner, StatusMark } from "../components/primitives";
import { onboard, pickDirectory } from "../lib/ipc";

// 화면 2. 온보딩 — 첫 실행 셋업(데이터 디렉토리 선택 · 로컬 임베딩 준비 · 로컬 계정 생성).
export function Onboarding({
  onDone,
  credPathFor,
}: {
  onDone: (dataDir: string) => void;
  credPathFor: (dataDir: string) => string;
}) {
  const [dataDir, setDataDir] = useState<string>("");
  const [pass, setPass] = useState("");
  const [pass2, setPass2] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function pick() {
    const dir = await pickDirectory("데이터 디렉토리 선택");
    if (dir) setDataDir(dir);
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (pass !== pass2) {
      setError("두 패스프레이즈가 일치하지 않습니다.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const ok = await onboard(pass, credPathFor(dataDir));
      if (ok) onDone(dataDir);
      else setError("자격 생성에 실패했습니다.");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="gate">
      <form className="gate__card" onSubmit={submit}>
        <div className="gate__brand">
          <Icon name="onboarding" size={28} />
          <h1>Vinc 설치</h1>
        </div>
        <p className="muted">
          데이터는 선택한 단일 폴더에 머뭅니다(스토어·inbox·packs·models). 모두 오프라인입니다.
        </p>

        <div className="field">
          <label>데이터 디렉토리</label>
          <div className="picker">
            <span className="picker__path">{dataDir || "폴더를 선택하세요"}</span>
            <Button type="button" variant="ghost" size="sm" icon="edit" onClick={pick}>
              선택
            </Button>
          </div>
        </div>

        <div className="field">
          <label htmlFor="p1">계정 패스프레이즈</label>
          <input
            id="p1"
            className="input"
            type="password"
            value={pass}
            onChange={(e) => setPass(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="p2">패스프레이즈 확인</label>
          <input
            id="p2"
            className="input"
            type="password"
            value={pass2}
            onChange={(e) => setPass2(e.target.value)}
          />
        </div>

        <div className="row">
          <StatusMark
            state={dataDir ? "success" : "idle"}
            label={dataDir ? "디렉토리 준비됨" : "디렉토리 미선택"}
          />
        </div>

        {error && <ErrorBanner message="설치 실패" detail={error} />}
        <Button
          type="submit"
          variant="primary"
          icon="check"
          disabled={busy || !dataDir || !pass || !pass2}
        >
          {busy ? "설치 중…" : "계정 생성 · 시작"}
        </Button>
      </form>
    </div>
  );
}
