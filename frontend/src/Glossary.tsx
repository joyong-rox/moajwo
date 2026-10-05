import { useEffect, useRef, useState } from 'react'
import {
  createAlias,
  deleteAlias,
  deleteEntry,
  GLOSSARY_EXPORT_URL,
  GLOSSARY_TEMPLATE_URL,
  glossaryInfo,
  importGlossary,
  listAliases,
  listEntries,
  type GlossaryAlias,
  type GlossaryEntry,
  type GlossaryInfo,
  type ImportReport,
} from './api'

const SOURCE_LABEL = { example: '예시', upload: '업로드', user: '직접 등록' } as const

function errorText(err: unknown) {
  return err instanceof Error ? err.message : String(err)
}

function period(e: GlossaryEntry) {
  if (!e.valid_from && !e.valid_to) return ''
  return `${e.valid_from ?? '처음'} ~ ${e.valid_to ?? '현재'}`
}

/** 용어사전: 사용자가 업로드·등록해 관리하는 기관·지역 이름 사전 (처음에는 예시 몇 개만 있음). */
function Glossary({ refreshKey, onChanged }: { refreshKey: number; onChanged: () => void }) {
  const fileRef = useRef<HTMLInputElement>(null)
  const [info, setInfo] = useState<GlossaryInfo | null>(null)
  const [entries, setEntries] = useState<GlossaryEntry[]>([])
  const [aliases, setAliases] = useState<GlossaryAlias[]>([])
  const [query, setQuery] = useState('')
  const [scope, setScope] = useState<'all' | 'org' | 'region'>('all')
  const [report, setReport] = useState<ImportReport | null>(null)
  const [target, setTarget] = useState<GlossaryEntry | null>(null)
  const [alias, setAlias] = useState('')
  const [validFrom, setValidFrom] = useState('')
  const [validTo, setValidTo] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [searchVersion, setSearchVersion] = useState(0)

  useEffect(() => {
    let cancelled = false
    Promise.all([glossaryInfo(), listAliases(), listEntries(query, scope)])
      .then(([i, a, e]) => {
        if (cancelled) return
        setInfo(i)
        setAliases(a)
        setEntries(e)
      })
      .catch((err: unknown) => !cancelled && setError(errorText(err)))
    return () => {
      cancelled = true
    }
    // 검색어 입력 중에는 다시 불러오지 않고, 검색 버튼·변경 시에만 불러온다
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey, searchVersion])

  async function act(fn: () => Promise<string>) {
    setBusy(true)
    setError(null)
    try {
      setMessage(await fn())
      onChanged()
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  async function upload(list: FileList | null) {
    const file = list?.[0]
    if (!file) return
    setBusy(true)
    setError(null)
    setMessage(null)
    try {
      const result = await importGlossary(file)
      setReport(result)
      if (result.applied) onChanged()
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  return (
    <section className="glossary">
      <h2>용어사전 (기관·지역 이름)</h2>
      {info && (
        <p className="upload-drop-hint">
          등록 항목 {info.entries}개 (자치단체 {info.region_entries}
          {info.example_entries > 0 && ` · 예시 ${info.example_entries}`}) · 별칭 {info.aliases}개 · 항목은 7자리
          코드로 식별, 시도 2자리·시군구 3자리 코드 확인 기준 {info.region_code_table_date}
        </p>
      )}

      <div className="glossary-upload">
        <button
          type="button"
          className="mapping-run"
          disabled={busy}
          onClick={() => fileRef.current?.click()}
        >
          {busy ? '처리 중…' : '용어사전 파일 업로드 (CSV·엑셀)'}
        </button>
        <input
          ref={fileRef}
          type="file"
          accept=".csv,.xlsx,.xls"
          hidden
          aria-label="용어사전 파일"
          onChange={(e) => void upload(e.target.files)}
        />
        <a href={GLOSSARY_TEMPLATE_URL} download>
          양식 내려받기
        </a>
        <a href={GLOSSARY_EXPORT_URL} download>
          현재 사전 내보내기
        </a>
      </div>
      <p className="upload-drop-hint">
        열: 코드(7자리) · 정식명칭 · 유형 · 시도코드(2자리) · 시군구코드(3자리) · 시작일 · 종료일 · 이전코드 · 별칭(;
        구분) · 메모. 같은 코드는 갱신되며, 오류가 한 행이라도 있으면 아무것도 반영하지 않습니다.
      </p>

      {report && (
        <div className={`glossary-report${report.applied ? '' : ' is-failed'}`} role="status">
          <p className="pii-title">
            {report.applied
              ? `반영 완료: ${report.rows}행 — 추가 ${report.inserted} · 갱신 ${report.updated} · 별칭 ${report.aliases_added}`
              : `반영하지 않음: 오류 ${report.errors.length}건을 고친 뒤 다시 올려 주세요`}
          </p>
          {report.errors.length + report.warnings.length > 0 && (
            <ul className="pii-items">
              {report.errors.map((e, i) => (
                <li key={`e${i}`}>
                  <strong>오류</strong> {e.row ? `${e.row}행: ` : ''}
                  {e.message}
                </li>
              ))}
              {report.warnings.map((w, i) => (
                <li key={`w${i}`}>
                  경고 {w.row ? `${w.row}행: ` : ''}
                  {w.message}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div className="glossary-search">
        <select aria-label="항목 범위" value={scope} onChange={(e) => setScope(e.target.value as typeof scope)}>
          <option value="all">전체</option>
          <option value="org">기관 (시도코드 없음)</option>
          <option value="region">자치단체 (시도코드 있음)</option>
        </select>
        <input
          type="search"
          placeholder="이름·코드·별칭으로 찾기"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && setSearchVersion((v) => v + 1)}
        />
        <button type="button" className="mapping-confirm" onClick={() => setSearchVersion((v) => v + 1)}>
          검색
        </button>
      </div>

      <div className="upload-table-wrap glossary-entries">
        <table className="upload-table">
          <thead>
            <tr>
              <th scope="col">이름</th>
              <th scope="col">코드</th>
              <th scope="col">시도·시군구</th>
              <th scope="col">유효기간</th>
              <th scope="col">별칭</th>
              <th scope="col">관리</th>
            </tr>
          </thead>
          <tbody>
            {entries.length === 0 && (
              <tr>
                <td colSpan={6} className="upload-drop-hint">
                  항목이 없습니다. 양식을 내려받아 업로드하세요.
                </td>
              </tr>
            )}
            {entries.map((e) => (
              <tr key={e.id} className={target?.id === e.id ? 'is-selected' : undefined}>
                <th scope="row">
                  {e.name}
                  <span className="glossary-meta">
                    {e.type ?? ''} · {SOURCE_LABEL[e.source]}
                    {!e.active && ' · 종료'}
                  </span>
                </th>
                <td>{e.code}</td>
                <td>{e.sido_code ? `${e.sido_code}${e.sigungu_code ? ` · ${e.sigungu_code}` : ''}` : ''}</td>
                <td>{period(e)}</td>
                <td className="glossary-alias-cell">{e.aliases.join(', ')}</td>
                <td>
                  <div className="mapping-pick">
                    <button type="button" className="mapping-confirm" onClick={() => setTarget(e)}>
                      별칭 대상
                    </button>
                    <button
                      type="button"
                      className="mapping-confirm"
                      disabled={busy}
                      onClick={() =>
                        void act(async () => {
                          await deleteEntry(e.id)
                          if (target?.id === e.id) setTarget(null)
                          return `'${e.name}' 항목과 별칭 삭제`
                        })
                      }
                    >
                      삭제
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3 className="glossary-subtitle">별칭 직접 등록</h3>
      <div className="validation-form glossary-form">
        <label>
          별칭 (자료에 적힌 이름)
          <input type="text" placeholder="예: 행안부" value={alias} onChange={(e) => setAlias(e.target.value)} />
        </label>
        <label>
          연결 대상
          <input type="text" readOnly value={target ? `${target.name} (${target.code})` : '위 목록에서 [별칭 대상] 선택'} />
        </label>
        <label>
          유효 시작일 (선택)
          <input type="date" value={validFrom} onChange={(e) => setValidFrom(e.target.value)} />
        </label>
        <label>
          유효 종료일 (선택)
          <input type="date" value={validTo} onChange={(e) => setValidTo(e.target.value)} />
        </label>
      </div>
      <button
        type="button"
        className="mapping-run"
        disabled={busy || !target || !alias.trim()}
        onClick={() =>
          void act(async () => {
            if (!target) return ''
            const created = await createAlias({
              alias: alias.trim(),
              target_code: target.code,
              valid_from: validFrom || null,
              valid_to: validTo || null,
            })
            setAlias('')
            setValidFrom('')
            setValidTo('')
            return `'${created.alias}' → ${created.target_name} 등록`
          })
        }
      >
        별칭 등록
      </button>
      {message && (
        <span className="mapping-message" role="status">
          {' '}
          {message}
        </span>
      )}
      {error && (
        <p className="upload-error" role="alert">
          {error}
        </p>
      )}

      {aliases.length > 0 && (
        <details className="glossary-aliases">
          <summary>별칭 전체 {aliases.length}개</summary>
          <div className="upload-table-wrap">
            <table className="upload-table">
              <thead>
                <tr>
                  <th scope="col">별칭</th>
                  <th scope="col">연결 대상</th>
                  <th scope="col">유효기간</th>
                  <th scope="col">삭제</th>
                </tr>
              </thead>
              <tbody>
                {aliases.map((a) => (
                  <tr key={a.id}>
                    <th scope="row">{a.alias}</th>
                    <td>
                      {a.target_name ?? '?'} ({a.target_code})
                    </td>
                    <td>
                      {a.valid_from || a.valid_to ? `${a.valid_from ?? '처음'} ~ ${a.valid_to ?? '현재'}` : '제한 없음'}
                    </td>
                    <td>
                      <button
                        type="button"
                        className="mapping-confirm"
                        disabled={busy}
                        onClick={() =>
                          void act(async () => {
                            await deleteAlias(a.id)
                            return `'${a.alias}' 별칭 삭제`
                          })
                        }
                      >
                        삭제
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </section>
  )
}

export default Glossary
