import { useState } from "react";
import { Icon } from "../components/Icon";
import { Button, ErrorBanner } from "../components/primitives";
import { sessionLogin } from "../lib/ipc";

// 화면 1. 로그인 — 로컬 계정 검증(F4, 네트워크 왕복 없음).
export function Login({ credPath, onLogin }: { credPath: string; onLogin: () => void }) {
  const [pass, setPass] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const ok = await sessionLogin(pass, credPath);
      if (ok) onLogin();
      else setError("패스프레이즈가 일치하지 않습니다.");
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
          <Icon name="login" size={28} />
          <h1>Vinc 로그인</h1>
        </div>
        <p className="muted">
          로컬 계정 패스프레이즈로 잠금을 해제합니다. 외부 인증 서버 왕복이 없습니다(오프라인).
        </p>
        <div className="field">
          <label htmlFor="pass">패스프레이즈</label>
          <input
            id="pass"
            className="input"
            type="password"
            autoFocus
            value={pass}
            onChange={(e) => setPass(e.target.value)}
            placeholder="로컬 계정 패스프레이즈"
          />
        </div>
        {error && <ErrorBanner message="로그인 실패" detail={error} />}
        <Button type="submit" variant="primary" icon="lock" disabled={busy || !pass}>
          {busy ? "확인 중…" : "잠금 해제"}
        </Button>
      </form>
    </div>
  );
}
