import { useState, type FormEvent } from 'react'
import { login, setupOwner, type Me } from './api'

/** 로그인 화면. 계정이 하나도 없으면(처음 실행) 최초 소유자 계정을 만든다. */
function Login({ setupRequired, onSignedIn }: { setupRequired: boolean; onSignedIn: (me: Me) => void }) {
  const [username, setUsername] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    if (setupRequired && password !== confirm) {
      setError('비밀번호 확인이 일치하지 않습니다.')
      return
    }
    setBusy(true)
    try {
      const me = setupRequired
        ? await setupOwner({ username, display_name: displayName, password })
        : await login(username, password)
      onSignedIn(me)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="login">
      <form className="login-card" onSubmit={(e) => void submit(e)}>
        <h1>Moajwo</h1>
        <p className="subtitle">문서취합 자동화 도구</p>
        <h2>{setupRequired ? '처음 사용 — 소유자 계정 만들기' : '로그인'}</h2>
        {setupRequired && (
          <p className="upload-drop-hint">
            아직 계정이 없습니다. 이 계정이 소유자(설정·계정 관리 권한)가 됩니다. 다른 사용자는 로그인 후 '사용자
            관리'에서 만듭니다.
          </p>
        )}
        <label>
          아이디
          <input
            name="username"
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
            maxLength={32}
          />
        </label>
        {setupRequired && (
          <label>
            이름
            <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} required maxLength={64} />
          </label>
        )}
        <label>
          비밀번호
          <input
            name="password"
            type="password"
            autoComplete={setupRequired ? 'new-password' : 'current-password'}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={setupRequired ? 8 : undefined}
            maxLength={128}
          />
        </label>
        {setupRequired && (
          <label>
            비밀번호 확인
            <input
              type="password"
              autoComplete="new-password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              required
            />
          </label>
        )}
        {error && (
          <p className="upload-error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" className="mapping-run" disabled={busy}>
          {busy ? '확인 중…' : setupRequired ? '소유자 계정 만들고 시작' : '로그인'}
        </button>
      </form>
    </main>
  )
}

export default Login
