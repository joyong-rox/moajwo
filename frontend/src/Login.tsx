import { useId, useState, type FormEvent } from "react";
import { login, setupOwner, signup, type Me } from "./api";

type Mode = "login" | "signup" | "signup-done";

const SETUP_NOTICE =
  "처음 생성하는 소유자 계정은 이 서비스의 최고 관리자입니다. 사용자 가입 승인, 계정 관리 및 서비스 설정을 담당합니다.";
const SIGNUP_NOTICE =
  "회원가입 후 최고 관리자(소유자)의 승인을 받아야 서비스를 이용할 수 있습니다.";

/** 로그인 화면. 계정이 하나도 없으면(처음 실행) 최고 관리자 계정을 만들고, 그 뒤에는 회원가입(승인 대기)도 받는다. */
function Login({
  setupRequired,
  onSignedIn,
}: {
  setupRequired: boolean;
  onSignedIn: (me: Me) => void;
}) {
  const [mode, setMode] = useState<Mode>("login");
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [doneMessage, setDoneMessage] = useState("");
  const usernameHelpId = useId();
  const passwordHelpId = useId();
  const confirmHelpId = useId();

  const creating = setupRequired || mode === "signup"; // 새 계정 입력(이름·비밀번호 확인)이 필요한 화면

  function switchMode(next: Mode) {
    setMode(next);
    setError(null);
    setPassword("");
    setConfirm("");
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (creating && password !== confirm) {
      setError("비밀번호 확인이 일치하지 않습니다.");
      return;
    }
    setBusy(true);
    try {
      if (setupRequired) {
        onSignedIn(
          await setupOwner({ username, display_name: displayName, password }),
        );
      } else if (mode === "signup") {
        const result = await signup({
          username,
          display_name: displayName,
          password,
          password_confirm: confirm,
        });
        setDoneMessage(result.message);
        setPassword("");
        setConfirm("");
        setMode("signup-done");
      } else {
        onSignedIn(await login(username, password));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  if (mode === "signup-done" && !setupRequired) {
    return (
      <main className="login">
        <section className="login-card" aria-label="가입 신청 완료">
          <h1>Moajwo</h1>
          <p className="subtitle">문서취합 자동화 도구</p>
          <h2>가입 신청 완료</h2>
          <p className="mapping-message" role="status">
            {doneMessage}
          </p>
          <p className="upload-drop-hint">신청한 아이디: {username}</p>
          <button
            type="button"
            className="mapping-run"
            onClick={() => switchMode("login")}
          >
            로그인 화면으로
          </button>
        </section>
      </main>
    );
  }

  const title = setupRequired
    ? "처음 사용 — 최고 관리자 계정 만들기"
    : mode === "signup"
      ? "회원가입"
      : "로그인";
  const submitLabel = setupRequired
    ? "최고 관리자 계정 만들기"
    : mode === "signup"
      ? "가입 신청"
      : "로그인";

  return (
    <main className="login">
      <form className="login-card" onSubmit={(e) => void submit(e)}>
        <h1>Moajwo</h1>
        <p className="subtitle">문서취합 자동화 도구</p>
        <h2>{title}</h2>
        {setupRequired && (
          <p className="upload-drop-hint login-notice">{SETUP_NOTICE}</p>
        )}
        {!setupRequired && mode === "signup" && (
          <p className="upload-drop-hint login-notice">{SIGNUP_NOTICE}</p>
        )}
        {/* 새 계정을 만들 때만 서버 규칙(auth.py USERNAME_RE·MIN_PASSWORD)을 입력 전에 보이고 칸과 연결한다 (입력해도 사라지지 않음) */}
        <div className="login-field">
          <label>
            아이디
            <input
              name="username"
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              maxLength={32}
              aria-describedby={creating ? usernameHelpId : undefined}
            />
          </label>
          {creating && (
            <p className="upload-drop-hint login-help" id={usernameHelpId}>
              영문 소문자 또는 숫자로 시작하고, 영문 소문자·숫자·마침표(.)·밑줄(_)·하이픈(-)만 써서 3~32자로 입력하세요.
            </p>
          )}
        </div>
        {creating && (
          <label>
            {setupRequired ? "이름" : "표시 이름"}
            <input
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              required
              maxLength={64}
            />
          </label>
        )}
        <div className="login-field">
          <label>
            비밀번호
            <input
              name="password"
              type="password"
              autoComplete={creating ? "new-password" : "current-password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={creating ? 8 : undefined}
              maxLength={128}
              aria-describedby={creating ? passwordHelpId : undefined}
            />
          </label>
          {creating && (
            <p className="upload-drop-hint login-help" id={passwordHelpId}>
              8~128자로 입력하세요.
            </p>
          )}
        </div>
        {creating && (
          <div className="login-field">
            <label>
              비밀번호 확인
              <input
                type="password"
                autoComplete="new-password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                required
                aria-describedby={confirmHelpId}
              />
            </label>
            <p className="upload-drop-hint login-help" id={confirmHelpId}>
              위에 입력한 비밀번호를 한 번 더 입력하세요 (잘못 입력하지 않았는지 확인합니다).
            </p>
          </div>
        )}
        {error && (
          <p className="upload-error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" className="mapping-run" disabled={busy}>
          {busy ? "확인 중…" : submitLabel}
        </button>
        {/* 최초 설정 전에는 가입을 받지 않는다 (서버도 409) */}
        {!setupRequired && (
          <p className="login-switch">
            {mode === "login" ? (
              <>
                계정이 없나요?{" "}
                <button
                  type="button"
                  className="login-link"
                  onClick={() => switchMode("signup")}
                >
                  회원가입
                </button>
              </>
            ) : (
              <>
                이미 계정이 있나요?{" "}
                <button
                  type="button"
                  className="login-link"
                  onClick={() => switchMode("login")}
                >
                  로그인
                </button>
              </>
            )}
          </p>
        )}
      </form>
    </main>
  );
}

export default Login;
