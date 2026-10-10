import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { createUser, listUsers, resetPassword, ROLE_LABEL, updateUser, type Role, type UserAccount } from './api'
import { useAuth } from './authContext'
import { formatTime } from './runFormat'

/** 사용자 관리: 관리자는 일반 사용자 계정만, 소유자는 모든 계정을 만들고 바꾼다. */
function Users() {
  const { me } = useAuth()
  const [users, setUsers] = useState<UserAccount[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [form, setForm] = useState({ username: '', display_name: '', role: 'user' as Role, password: '' })
  const roles: Role[] = me.role === 'owner' ? ['user', 'admin', 'owner'] : ['user']

  const load = useCallback(() => {
    listUsers()
      .then((list) => {
        setUsers(list)
        setError(null)
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)))
  }, [])

  useEffect(() => {
    load()
  }, [load])

  async function run(action: () => Promise<unknown>, done: string) {
    setError(null)
    setMessage(null)
    try {
      await action()
      setMessage(done)
      load()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  function submit(e: FormEvent) {
    e.preventDefault()
    void run(async () => {
      await createUser(form)
      setForm({ username: '', display_name: '', role: 'user', password: '' })
    }, `계정 '${form.username}'을(를) 만들었습니다.`)
  }

  const canManage = (u: UserAccount) => me.role === 'owner' || u.role === 'user'

  return (
    <section className="admin" aria-label="사용자 관리">
      <h2>사용자 관리</h2>
      <p className="upload-drop-hint">
        일반 사용자: 자기 파일·검토·실행 기록만 · 관리자: 전체 조회(읽기 전용)·사전 관리·승격 심사·일반 사용자 계정 관리 ·
        소유자: 관리자 권한 + 설정·모든 계정 관리. 어떤 역할도 통합 결과 데이터를 직접 수정할 수 없습니다.
      </p>
      <form className="admin-form" onSubmit={submit}>
        <label>
          아이디
          <input
            value={form.username}
            onChange={(e) => setForm({ ...form, username: e.target.value })}
            required
            maxLength={32}
            placeholder="영문 소문자·숫자"
          />
        </label>
        <label>
          이름
          <input
            value={form.display_name}
            onChange={(e) => setForm({ ...form, display_name: e.target.value })}
            required
            maxLength={64}
          />
        </label>
        <label>
          역할
          <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as Role })}>
            {roles.map((r) => (
              <option key={r} value={r}>
                {ROLE_LABEL[r]}
              </option>
            ))}
          </select>
        </label>
        <label>
          처음 비밀번호
          <input
            type="password"
            autoComplete="new-password"
            value={form.password}
            onChange={(e) => setForm({ ...form, password: e.target.value })}
            required
            minLength={8}
            maxLength={128}
          />
        </label>
        <button type="submit" className="mapping-run">
          계정 만들기
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
      {users && (
        <div className="upload-table-wrap">
          <table className="upload-table">
            <thead>
              <tr>
                <th scope="col">아이디</th>
                <th scope="col">이름</th>
                <th scope="col">역할</th>
                <th scope="col">상태</th>
                <th scope="col">마지막 로그인</th>
                <th scope="col">관리</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id}>
                  <th scope="row">{u.username}</th>
                  <td>{u.display_name}</td>
                  <td>
                    {canManage(u) && u.id !== me.id ? (
                      <select
                        aria-label={`${u.username} 역할`}
                        value={u.role}
                        onChange={(e) =>
                          void run(
                            () => updateUser(u.id, { role: e.target.value as Role }),
                            `${u.username}의 역할을 바꿨습니다 (다시 로그인하면 적용).`,
                          )
                        }
                      >
                        {(me.role === 'owner' ? (['user', 'admin', 'owner'] as Role[]) : (['user'] as Role[])).map(
                          (r) => (
                            <option key={r} value={r}>
                              {ROLE_LABEL[r]}
                            </option>
                          ),
                        )}
                      </select>
                    ) : (
                      u.role_label
                    )}
                  </td>
                  <td>{u.is_active ? '사용 중' : '사용 중지'}</td>
                  <td>{formatTime(u.last_login_at)}</td>
                  <td className="admin-row-actions">
                    {canManage(u) && u.id !== me.id && (
                      <>
                        <button
                          type="button"
                          className="mapping-confirm"
                          onClick={() =>
                            void run(
                              () => updateUser(u.id, { is_active: !u.is_active }),
                              `${u.username}을(를) ${u.is_active ? '사용 중지' : '다시 사용'}했습니다.`,
                            )
                          }
                        >
                          {u.is_active ? '사용 중지' : '다시 사용'}
                        </button>
                        <button
                          type="button"
                          className="mapping-confirm"
                          onClick={() => {
                            const password = window.prompt(`${u.username}의 새 비밀번호 (8자 이상)`)
                            if (password) void run(() => resetPassword(u.id, password), `${u.username}의 비밀번호를 바꿨습니다.`)
                          }}
                        >
                          비밀번호 재설정
                        </button>
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

export default Users
