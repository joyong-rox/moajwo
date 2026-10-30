import { useState, type FormEvent } from "react";
import { login, type Me } from "./api";

const INIT_NOTICE =
  "아직 초기화되지 않은 설치입니다. 설치 담당자가 초기화 명령(python -m app.adminctl init)으로 관리자 계정을 만든 뒤에 로그인할 수 있습니다.";
const ACCOUNT_NOTICE =
  "계정이 없나요? 계정은 관리자에게 요청해 주세요. 관리자가 발급한 임시 비밀번호로 처음 로그인하면 본인 비밀번호를 정하는 화면이 열립니다.";

/** 로그인 화면. 관리자(admin)도 일반 사용자도 같은 화면으로 로그인한다. 공개 회원가입·최초 설정 화면은 없다 —
 * 초기화 전(계정 0개)에는 설치 담당자의 초기화가 필요하다는 안내만 보인다. */
function Login({
  setupRequired,
  unavailable,
  notice,
  onSignedIn,
}: {
  setupRequired: boolean; // 초기화 전 — 서버의 /auth/status.setup_required
  unavailable?: string; // 처음 상태 확인이 실패한 이유 (서버·DB 연결) — 로그인 화면에 이유만 보인다
  notice?: string; // 직전 동작의 안내 (예: 비밀번호를 바꿔 다시 로그인해야 함)
  onSignedIn: (me: Me) => void;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      onSignedIn(await login(username, password));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  if (setupRequired) {
    return (
      <main className="login">
        <section className="login-card" aria-label="초기화 필요">
          <h1>Moajwo</h1>
          <p className="subtitle">문서취합 자동화 도구</p>
          <h2>설치 담당자의 초기화가 필요합니다</h2>
          <p className="upload-drop-hint login-notice" role="status">
            {INIT_NOTICE}
          </p>
        </section>
      </main>
    );
  }

  return (
    <main className="login">
      <form className="login-card" onSubmit={(e) => void submit(e)}>
        <h1>Moajwo</h1>
        <p className="subtitle">문서취합 자동화 도구</p>
        <h2>로그인</h2>
        {unavailable && (
          <p className="upload-error" role="alert">
            서버 상태를 확인하지 못했습니다: {unavailable} 잠시 뒤 새로고침하세요.
          </p>
        )}
        {notice && (
          <p className="mapping-message" role="status">
            {notice}
          </p>
        )}
        <div className="login-field">
          <label>
            아이디
            <input
              name="username"
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              maxLength={64}
            />
          </label>
        </div>
        <div className="login-field">
          <label>
            비밀번호
            <input
              name="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              maxLength={256}
            />
          </label>
        </div>
        {error && (
          <p className="upload-error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" className="mapping-run" disabled={busy}>
          {busy ? "확인 중…" : "로그인"}
        </button>
        <p className="login-switch">{ACCOUNT_NOTICE}</p>
      </form>
    </main>
  );
}

export default Login;
