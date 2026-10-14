import { useEffect, useMemo, useState } from 'react'
import {
  fetchCommonReview,
  listFiles,
  type BatchTarget,
  type CommonCategory,
  type CommonGroup,
  type CommonMember,
  type CommonReview,
  type StandardFieldInfo,
  type StoredFile,
} from './api'
import BatchConfirm from './BatchConfirm'
import FieldPicker from './FieldPicker'
import { formatCell, loadFields, NONE, NONE_HELP, STATUS_TEXT, VALUE_KIND_LABEL } from './mappingText'
import { navigate } from './router'

const CATEGORY: Record<CommonCategory, { label: string; help: string }> = {
  common: {
    label: '공통 매핑 후보',
    help: '여러 파일에서 이름·표준 항목·구분어·값 형태·단위가 모두 같은 열입니다. 그래도 값과 파일 맥락을 확인한 뒤 확정하세요.',
  },
  divergent: {
    label: '파일별 판단이 다른 항목',
    help: '같은 이름이지만 매핑·단위·값 형태 등이 다르거나, 이름만으로 같은 대상인지 알 수 없는 열입니다. 파일별로 판단하세요.',
  },
  unresolved: { label: '미해결 항목', help: '표준 항목 제안이 없는 열입니다.' },
  only: { label: '특정 파일에만 있는 항목', help: '선택한 파일 중 한 파일에만 있는 이름입니다.' },
}
const ORDER: CommonCategory[] = ['common', 'divergent', 'unresolved', 'only']

const memberKey = (m: CommonMember) => `${m.file_id}|${m.sheet}|${m.column_id}`

/** 여러 파일 공통 항목 검토: 저장된 분석만 모은다 (화면을 열어도 다시 분석·모델 호출 없음) */
function MappingCommon() {
  const [files, setFiles] = useState<StoredFile[] | null>(null)
  const [picked, setPicked] = useState<Set<number>>(new Set())
  const [review, setReview] = useState<CommonReview | null>(null)
  const [requested, setRequested] = useState<number[] | null>(null)
  const [category, setCategory] = useState<CommonCategory>('common')
  const [groupId, setGroupId] = useState<string | null>(null)
  const [fields, setFields] = useState<StandardFieldInfo[]>([])
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    listFiles()
      .then(setFiles)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)))
    loadFields()
      .then(setFields)
      .catch(() => undefined)
  }, [])

  useEffect(() => {
    if (!requested) return
    let alive = true
    fetchCommonReview(requested)
      .then((r) => {
        if (!alive) return
        setReview(r)
        setLoading(false)
      })
      .catch((err: unknown) => {
        if (!alive) return
        setError(err instanceof Error ? err.message : String(err))
        setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [requested])

  const counts = useMemo(() => {
    const c: Record<string, number> = {}
    for (const g of review?.groups ?? []) c[g.category] = (c[g.category] ?? 0) + 1
    return c
  }, [review])
  const groups = (review?.groups ?? []).filter((g) => g.category === category)
  const group = review?.groups.find((g) => g.group_id === groupId) ?? null

  function open() {
    setError(null)
    setMessage(null)
    setGroupId(null)
    setLoading(true)
    setRequested([...picked])
  }

  return (
    <section className="mw mw-common" aria-label="여러 파일 공통 항목 검토">
      <p className="upload-drop-hint">
        각 파일에서 이미 실행한 '열 매핑 분석' 결과와 지금의 확정값만 모아 비교합니다. 이 화면을 열어도 다시 분석하거나
        모델을 부르지 않습니다. 같은 열 이름이라는 이유만으로 같은 항목으로 묶지 않습니다.
      </p>
      <fieldset className="mw-files">
        <legend>검토할 파일 (최대 20개)</legend>
        {files === null && <p className="mw-muted">불러오는 중…</p>}
        {files?.length === 0 && <p className="mw-muted">올린 파일이 없습니다.</p>}
        <div className="mw-file-list">
          {(files ?? []).map((f) => (
            <label key={f.id} className="mw-file-option">
              <input
                type="checkbox"
                checked={picked.has(f.id)}
                disabled={!picked.has(f.id) && picked.size >= 20}
                onChange={(e) =>
                  setPicked((prev) => {
                    const next = new Set(prev)
                    if (e.target.checked) next.add(f.id)
                    else next.delete(f.id)
                    return next
                  })
                }
              />
              #{f.id} {f.original_name}
            </label>
          ))}
        </div>
        <button type="button" className="mapping-run" disabled={picked.size === 0 || loading} onClick={open}>
          {loading ? '모으는 중…' : `공통 검토 열기 (${picked.size}개 파일)`}
        </button>
      </fieldset>
      {error && (
        <p className="upload-error" role="alert">
          {error}
        </p>
      )}
      {message && (
        <p className="mapping-message" role="status">
          {message}
        </p>
      )}
      {review && review.missing.length > 0 && (
        <div className="mapping-warn" role="status">
          분석 기록이 없어 제외한 파일 (자동으로 분석하지 않음):
          <ul>
            {review.missing.map((m) => (
              <li key={m.file_id}>
                <a
                  href={`/mapping?file=${m.file_id}`}
                  onClick={(e) => {
                    e.preventDefault()
                    navigate(`/mapping?file=${m.file_id}`)
                  }}
                >
                  #{m.file_id} {m.name}
                </a>{' '}
                — 파일 화면에서 '열 매핑 분석'을 실행하세요
              </li>
            ))}
          </ul>
        </div>
      )}
      {review?.files.some((f) => f.stale) && (
        <p className="mapping-warn" role="status">
          파일 구성이 분석 때와 달라 묶지 않은 파일:{' '}
          {review.files
            .filter((f) => f.stale)
            .map((f) => f.name)
            .join(', ')}{' '}
          — 다시 분석하세요.
        </p>
      )}
      {review && (
        <>
          <div className="admin-filters" role="tablist" aria-label="검토 구분">
            {ORDER.map((c) => (
              <button
                key={c}
                type="button"
                role="tab"
                aria-selected={category === c}
                className={`admin-filter${category === c ? ' is-active' : ''}`}
                onClick={() => {
                  setCategory(c)
                  setGroupId(null)
                }}
              >
                {CATEGORY[c].label} {counts[c] ?? 0}
              </button>
            ))}
          </div>
          <p className="mw-muted">{CATEGORY[category].help}</p>
          <div className={`mw-common-main${group ? ' has-detail' : ''}`}>
            <ul className="mw-groups" aria-label={CATEGORY[category].label}>
              {groups.length === 0 && <li className="mw-muted">해당 항목이 없습니다.</li>}
              {groups.map((g) => (
                <li key={g.group_id}>
                  <button
                    type="button"
                    className={`mw-group${g.group_id === groupId ? ' is-selected' : ''}`}
                    aria-pressed={g.group_id === groupId}
                    onClick={() => setGroupId(g.group_id)}
                  >
                    <strong className="mw-colname">{g.name}</strong>
                    <span className="mw-muted">
                      파일 {g.files}개 · 열 {g.members.length}개{g.field_label ? ` · ${g.field_label}` : ''}
                    </span>
                    {g.reasons.length > 0 && <span className="mw-reason-line">{g.reasons[0]}</span>}
                  </button>
                </li>
              ))}
            </ul>
            {group && (
              <GroupDetail
                key={group.group_id}
                group={group}
                fields={fields}
                onDone={(text) => {
                  setMessage(text)
                  setLoading(true)
                  setRequested((prev) => (prev ? [...prev] : prev))
                }}
                onClose={() => setGroupId(null)}
              />
            )}
          </div>
        </>
      )}
    </section>
  )
}

function GroupDetail({
  group,
  fields,
  onDone,
  onClose,
}: {
  group: CommonGroup
  fields: StandardFieldInfo[]
  onDone: (message: string) => void
  onClose: () => void
}) {
  // 기본 선택: 공통 후보 묶음에서 바꿀 수 있고 아직 확정하지 않은 열만. 다른 구분은 사용자가 직접 고른다
  const [chosen, setChosen] = useState<Set<string>>(
    () =>
      new Set(
        group.category === 'common' ? group.members.filter((m) => m.can_change && !m.confirmed).map(memberKey) : [],
      ),
  )
  const [target, setTarget] = useState<string>(group.category === 'common' ? (group.field_key ?? '') : '')
  const [batch, setBatch] = useState<BatchTarget[] | null>(null)
  const selectable = group.members.filter((m) => m.can_change)

  return (
    <aside className="mw-detail mw-group-detail" aria-label="묶음 상세">
      <button type="button" className="mapping-confirm" onClick={onClose}>
        ← 목록으로
      </button>
      <h3 className="mw-colname">{group.name}</h3>
      <p>
        <span className="upload-tag">{CATEGORY[group.category].label}</span> 파일 {group.files}개 · 열{' '}
        {group.members.length}개
      </p>
      {group.reasons.length > 0 && (
        <ul className="mw-problems">
          {group.reasons.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      )}
      <div className="upload-table-wrap">
        <table className="upload-table mw-members">
          <thead>
            <tr>
              <th scope="col">선택</th>
              <th scope="col">파일 · 시트 · 열</th>
              <th scope="col">현재 매핑</th>
              <th scope="col">값 예시</th>
              <th scope="col">값 형태 · 단위</th>
            </tr>
          </thead>
          <tbody>
            {group.members.map((m) => {
              const key = memberKey(m)
              return (
                <tr key={key}>
                  <td data-label="선택">
                    <div className="mw-cell">
                      <input
                        type="checkbox"
                        aria-label={`${m.file_name} ${m.sheet} ${m.column} 선택`}
                        checked={chosen.has(key)}
                        disabled={!m.can_change}
                        onChange={(e) =>
                          setChosen((prev) => {
                            const next = new Set(prev)
                            if (e.target.checked) next.add(key)
                            else next.delete(key)
                            return next
                          })
                        }
                      />
                      {!m.can_change && <span className="mw-muted mw-block">조회 전용</span>}
                    </div>
                  </td>
                  <td data-label="파일 · 시트 · 열">
                    <div className="mw-cell">
                      <span className="mw-block">{m.file_name}</span>
                      <span className="mw-muted mw-block">
                        {m.sheet} · {m.column}
                      </span>
                      <a
                        href={`/mapping?file=${m.file_id}&sheet=${encodeURIComponent(m.sheet)}&col=${m.column_id}`}
                        onClick={(e) => {
                          e.preventDefault()
                          navigate(`/mapping?file=${m.file_id}&sheet=${encodeURIComponent(m.sheet)}&col=${m.column_id}`)
                        }}
                      >
                        파일 화면에서 보기
                      </a>
                    </div>
                  </td>
                  <td data-label="현재 매핑">
                    <div className="mw-cell">
                      {m.field_label ?? (m.confirmed ? '해당 없음' : '미매핑')}{' '}
                      <span className={`upload-tag mapping-${m.status}`}>{STATUS_TEXT[m.status]}</span>
                      {m.qualifier && <span className="upload-tag">{m.qualifier}</span>}
                    </div>
                  </td>
                  <td data-label="값 예시" className="mw-sample-cell">
                    <div className="mw-cell">{m.samples.map(formatCell).join(', ') || '-'}</div>
                  </td>
                  <td data-label="값 형태 · 단위">
                    <div className="mw-cell">
                      {VALUE_KIND_LABEL[m.value_kind] ?? m.value_kind}
                      {m.unit && ` · ${m.unit}`}
                      {m.basis && ` · ${m.basis === '흐름' ? '월별 실적' : '시점 보유량'}`}
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <FieldPicker
        fields={fields}
        value={target}
        onChange={setTarget}
        label="선택한 열에 적용할 표준 항목"
        suggested={group.field_key ? [group.field_key] : []}
      />
      <p className="mw-muted">{NONE_HELP}</p>
      <div className="admin-actions">
        <button
          type="button"
          className="mapping-run"
          disabled={chosen.size === 0 || target === '' || selectable.length === 0}
          onClick={() =>
            setBatch(
              group.members
                .filter((m) => chosen.has(memberKey(m)))
                .map((m) => ({
                  file_id: m.file_id,
                  sheet: m.sheet,
                  column_id: m.column_id,
                  field_key: target === NONE ? null : target,
                })),
            )
          }
        >
          선택한 {chosen.size}개 열 확정 준비
        </button>
        <button
          type="button"
          className="mapping-confirm"
          onClick={() => setChosen(new Set())}
          disabled={chosen.size === 0}
        >
          선택 해제
        </button>
      </div>
      {batch && (
        <BatchConfirm
          targets={batch}
          title={`'${group.name}' 일괄 확정`}
          onCancel={() => setBatch(null)}
          onDone={(text) => {
            setBatch(null)
            onDone(text)
          }}
        />
      )}
    </aside>
  )
}

export default MappingCommon
