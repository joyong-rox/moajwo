import { useState, type FormEvent } from 'react'
import { changePassword } from './api'
import { useAuth } from './authContext'

/** 내 계정: 비밀번호 변경 (다른 기기의 로그인은 끊긴다). 최고 관리자(admin)도 여기서 본인 비밀번호를 바꾼다. */
function Account() {
  const { me } = useAuth()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setMessage(null)
    if (next !== confirm) {
      setError('새 비밀번호 확인이 일치하지 않습니다.')
      return
    }
    try {
      await changePassword(current, next, confirm)
      setCurrent('')
      setNext('')
      setConfirm('')
      setMessage('비밀번호를 바꿨습니다. 다른 기기의 로그인은 끊겼습니다.')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <section className="admin" aria-label="내 계정">
      <h2>내 계정</h2>
      <p>
        {me.display_name} ({me.username}) · {me.role_label}
      </p>
      <form className="admin-form" onSubmit={(e) => void submit(e)}>
        <label>
          현재 비밀번호
          <input
            type="password"
            autoComplete="current-password"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
            required
          />
        </label>
        <label>
          새 비밀번호 (8자 이상)
          <input
            type="password"
            autoComplete="new-password"
            value={next}
            onChange={(e) => setNext(e.target.value)}
            required
            minLength={8}
            maxLength={128}
          />
        </label>
        <label>
          새 비밀번호 확인
          <input
            type="password"
            autoComplete="new-password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            required
            maxLength={128}
          />
        </label>
        <button type="submit" className="mapping-run">
          비밀번호 변경
        </button>
      </form>
      {message && (
        <p className="mapping-message" role="status">
          {message}
        </p>
      )}
      {error && (
        <p className="upload-error" role="alert">
          {error}
        </p>
      )}
    </section>
  )
}

export default Account
