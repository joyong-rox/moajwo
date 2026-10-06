import { useCallback, useEffect, useState } from 'react'
import { deleteStandardTerm, listStandardTerms, type StandardTerm } from './api'
import Glossary from './Glossary'
import { formatTime } from './runFormat'

/** 사전 관리 (관리자·소유자): 기관·지역 용어사전 + 전사 표준 사전(승격된 열 이름) */
function Dictionary() {
  const [glossaryVersion, setGlossaryVersion] = useState(0)
  return (
    <>
      <StandardTerms />
      <Glossary refreshKey={glossaryVersion} onChanged={() => setGlossaryVersion((v) => v + 1)} />
    </>
  )
}

function StandardTerms() {
  const [terms, setTerms] = useState<StandardTerm[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    listStandardTerms()
      .then((list) => {
        setTerms(list)
        setError(null)
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)))
  }, [])

  useEffect(() => {
    load()
  }, [load])

  return (
    <section className="admin" aria-label="전사 표준 사전">
      <h2>전사 표준 사전 (승격된 열 이름)</h2>
      <p className="upload-drop-hint">
        승격 심사에서 승인한 열 이름입니다. 모든 사용자의 열 매핑 1단계에 적용됩니다. 기본 표준 사전과 겹치면 기본 사전이
        우선합니다.
      </p>
      {error && (
        <p className="upload-error" role="alert">
          {error}
        </p>
      )}
      {terms !== null && terms.length === 0 && <p className="upload-drop-hint">아직 승격된 열 이름이 없습니다.</p>}
      {terms !== null && terms.length > 0 && (
        <div className="upload-table-wrap">
          <table className="upload-table">
            <thead>
              <tr>
                <th scope="col">열 이름</th>
                <th scope="col">표준 항목</th>
                <th scope="col">승인</th>
                <th scope="col">관리</th>
              </tr>
            </thead>
            <tbody>
              {terms.map((t) => (
                <tr key={t.id}>
                  <th scope="row">{t.term}</th>
                  <td>{t.field_label ?? t.field_key}</td>
                  <td>
                    {t.approved_by_name ?? '-'} · {formatTime(t.created_at)}
                  </td>
                  <td>
                    <button
                      type="button"
                      className="mapping-confirm"
                      onClick={() => {
                        if (!window.confirm(`'${t.term}'을(를) 전사 표준 사전에서 뺄까요? 이미 만든 결과는 그대로입니다.`))
                          return
                        deleteStandardTerm(t.id)
                          .then(load)
                          .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)))
                      }}
                    >
                      빼기
                    </button>
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

export default Dictionary
