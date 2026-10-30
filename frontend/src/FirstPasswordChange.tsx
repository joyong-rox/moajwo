import { useId, useState, type FormEvent } from "react";
import { changePassword, type Me } from "./api";

/** 임시 비밀번호로 로그인한 직후의 화면: 본인 비밀번호를 정하기 전에는 업무 화면이 열리지 않는다 (서버도 업무 API를 403으로 막는다).
 * 바꾸면 서버가 세션을 모두 끝내므로 새 비밀번호로 다시 로그인한다. */
function FirstPasswordChange({
  me,
  onDone,
  onSignOut,
}: {
  me: Me;
  onDone: () => void; // 바꿨다 — 로그인 화면으로 (서버가 세션을 끝냈다)
  onSignOut: () => void;
}) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const currentHelpId = useId();
  const nextHelpId = useId();
  const confirmHelpId = useId();
  const expires = me.temp_password_expires_at
    ? new Date(me.temp_password_expires_at).toLocaleString("ko-KR", { dateStyle: "medium", timeStyle: "short" })
    : null;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (next !== confirm) {
      setError("새 비밀번호 확인이 일치하지 않습니다.");
      return;
    }
    if (next === current) {
      setError("새 비밀번호는 임시 비밀번호와 달라야 합니다.");
      return;
    }
    setBusy(true);
    try {
      await changePassword(current, next, confirm);
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login">
      <form className="login-card" onSubmit={(e) => void submit(e)} aria-labelledby="first-change-title">
        <h1>Moajwo</h1>
        <p className="subtitle">문서취합 자동화 도구</p>
        <h2 id="first-change-title">본인 비밀번호 설정</h2>
        <p className="upload-drop-hint login-notice" role="status">
          {me.display_name} ({me.username}) 님, 관리자가 발급한 임시 비밀번호로 로그인했습니다. 업무 화면을 쓰려면 본인 비밀번호로 바꾼 뒤 새 비밀번호로 다시
          로그인해야 합니다.
          {expires && ` 임시 비밀번호는 ${expires}까지만 쓸 수 있습니다 (지나면 관리자에게 재발급을 요청하세요).`}
        </p>
        <div className="login-field">
          <label>
            임시 비밀번호 (지금 로그인에 쓴 것)
            <input
              name="current-password"
              type="password"
              autoComplete="current-password"
              value={current}
              onChange={(e) => setCurrent(e.target.value)}
              required
              maxLength={256}
              aria-describedby={currentHelpId}
            />
          </label>
          <p className="upload-drop-hint login-help" id={currentHelpId}>
            관리자에게 받은 임시 비밀번호를 한 번 더 입력합니다.
          </p>
        </div>
        <div className="login-field">
          <label>
            새 비밀번호
            <input
              name="new-password"
              type="password"
              autoComplete="new-password"
              value={next}
              onChange={(e) => setNext(e.target.value)}
              required
              minLength={8}
              maxLength={128}
              aria-describedby={nextHelpId}
            />
          </label>
          <p className="upload-drop-hint login-help" id={nextHelpId}>
            8~128자로, 임시 비밀번호와 다르게 정하세요.
          </p>
        </div>
        <div className="login-field">
          <label>
            새 비밀번호 확인
            <input
              name="new-password-confirm"
              type="password"
              autoComplete="new-password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              required
              maxLength={128}
              aria-describedby={confirmHelpId}
            />
          </label>
          <p className="upload-drop-hint login-help" id={confirmHelpId}>
            위에 입력한 새 비밀번호를 한 번 더 입력하세요.
          </p>
        </div>
        {error && (
          <p className="upload-error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" className="mapping-run" disabled={busy}>
          {busy ? "바꾸는 중…" : "비밀번호 바꾸고 다시 로그인"}
        </button>
        <p className="login-switch">
          지금은 비밀번호 변경과 로그아웃만 할 수 있습니다.{" "}
          <button type="button" className="login-link" onClick={onSignOut} disabled={busy}>
            로그아웃
          </button>
        </p>
      </form>
    </main>
  );
}

export default FirstPasswordChange;
