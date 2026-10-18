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
import { Banner, Icon, StatusCards, WorkflowSteps, type IconName, type Tone } from './Workflow'

const CATEGORY: Record<CommonCategory, { label: string; help: string }> = {
  common: {
    label: '공통 매핑 후보',
    help: "여러 파일에서 열 이름이 같고 확인한 근거(표준 항목·구분어·값 형태, 수량 열은 단위·흐름/시점 표기)가 충돌하지 않는 열입니다. 묶음마다 '확인한 근거'에 실제로 확인한 것과 확인하지 못한 것을 적었으니, 값과 파일 맥락을 보고 확정하세요.",
  },
  divergent: {
    label: '파일별 판단이 다른 항목',
    help: '같은 이름이지만 매핑·단위·값 형태·흐름/시점 표기가 다르거나, 단위 표기가 불명확하거나 일부 파일에만 있거나, 이름만으로 같은 대상인지 알 수 없는 열입니다. 파일별로 판단하세요.',
  },
  unresolved: { label: '미해결 항목', help: '표준 항목 제안이 없는 열입니다.' },
  only: { label: '특정 파일에만 있는 항목', help: '선택한 파일 중 한 파일에만 있는 이름입니다.' },
}
const ORDER: CommonCategory[] = ['common', 'divergent', 'unresolved', 'only']
const CATEGORY_LOOK: Record<CommonCategory, { icon: IconName; tone: Tone }> = {
  common: { icon: 'link', tone: 'auto' },
  divergent: { icon: 'alert', tone: 'warn' },
  unresolved: { icon: 'question', tone: 'unresolved' },
  only: { icon: 'one', tone: 'muted' },
}
const MAX_PICK = 20 // 서버의 공통 검토 파일 수 제한과 같음 (mapping_review.COMMON_FILES_MAX)

const memberKey = (m: CommonMember) => `${m.file_id}|${m.sheet_id}|${m.column_id}`

const BASIS_TEXT: Record<NonNullable<CommonMember['basis']>, string> = {
  flow: '흐름(월별 실적) 표기',
  stock: '시점(보유량) 표기',
  conflict: '흐름·시점 표기가 함께 있음',
}

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
  const [query, setQuery] = useState('')

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
        // 묶음이 있는 첫 구분을 연다 (빈 구분부터 보이지 않게, 사용자가 고른 구분에 묶음이 있으면 그대로)
        setCategory((prev) =>
          r.groups.some((g) => g.category === prev) ? prev : (ORDER.find((c) => r.groups.some((g) => g.category === c)) ?? prev),
        )
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

  const q = query.replace(/\s+/g, '').toLowerCase()
  const shownFiles = (files ?? []).filter(
    (f) => !q || `#${f.id}${f.display_name}`.replace(/\s+/g, '').toLowerCase().includes(q),
  )
  const hiddenPicked = [...picked].filter((id) => !shownFiles.some((f) => f.id === id)).length
  const nameOf = (id: number) => files?.find((f) => f.id === id)?.display_name ?? `#${id}`
  const scope = requested ?? []

  function open() {
    setError(null)
    setMessage(null)
    setGroupId(null)
    setLoading(true)
    setRequested([...picked])
  }

  return (
    <section className="mw mw-common wk" aria-label="여러 파일 공통 항목 검토">
      <WorkflowSteps
        steps={[
          { key: 'files', state: picked.size ? 'done' : 'todo', detail: `고른 파일 ${picked.size}개`, href: '/' },
          {
            key: 'mapping',
            current: true,
            state: !review ? 'todo' : (counts.divergent ?? 0) + (counts.unresolved ?? 0) > 0 ? 'attention' : 'done',
            detail: review
              ? `공통 후보 ${counts.common ?? 0} · 판단이 다름 ${counts.divergent ?? 0} · 미해결 ${counts.unresolved ?? 0}`
              : '공통 검토를 열면 확인',
          },
          { key: 'review', state: 'todo', detail: '고른 파일로 검사', href: picked.size ? `/?files=${[...picked].join(',')}` : '/' },
          { key: 'result', state: 'todo', detail: '실행 기록', href: '/runs' },
        ]}
      />
      <p className="upload-drop-hint">
        각 파일에서 이미 실행한 '열 매핑 분석' 결과와 지금의 확정값만 모아 비교합니다. 이 화면을 열어도 다시 분석하거나
        모델을 부르지 않습니다. 같은 열 이름이라는 이유만으로 같은 항목으로 묶지 않습니다.
      </p>
      <fieldset className="mw-files" id="wk-common-files">
        <legend>검토할 파일 (최대 {MAX_PICK}개)</legend>
        {files === null && <p className="mw-muted">불러오는 중…</p>}
        {files?.length === 0 && <p className="mw-muted">올린 파일이 없습니다.</p>}
        {files && files.length > 0 && (
          <div className="wk-pickbar">
            <input
              type="search"
              className="mw-search"
              placeholder="파일 번호·이름 검색"
              aria-label="공통 검토할 파일 검색"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <span className="wk-pickcount" role="status">
              고른 파일 <strong>{picked.size}</strong>개 (최대 {MAX_PICK}) · 전체 {files.length}개
            </span>
            {picked.size > 0 && (
              <button type="button" className="login-link" onClick={() => setPicked(new Set())}>
                선택 모두 해제
              </button>
            )}
          </div>
        )}
        {hiddenPicked > 0 && (
          <p className="mapping-warn" role="status">
            고른 파일 중 {hiddenPicked}개가 검색으로 숨겨져 있습니다 — 숨겨져도 검토 대상에 들어갑니다.{' '}
            <button type="button" className="login-link" onClick={() => setQuery('')}>
              검색 지우기
            </button>
          </p>
        )}
        <div className="mw-file-list">
          {shownFiles.map((f) => (
            <label key={f.id} className={`mw-file-option wk-filepick${picked.has(f.id) ? ' is-picked' : ''}`}>
              <input
                type="checkbox"
                checked={picked.has(f.id)}
                disabled={!picked.has(f.id) && picked.size >= MAX_PICK}
                onChange={(e) =>
                  setPicked((prev) => {
                    const next = new Set(prev)
                    if (e.target.checked) next.add(f.id)
                    else next.delete(f.id)
                    return next
                  })
                }
              />
              #{f.id} {f.display_name}
            </label>
          ))}
        </div>
        <div className="admin-actions">
          <button type="button" className="mapping-run" disabled={picked.size === 0 || loading} onClick={open}>
            {loading ? '모으는 중…' : `공통 검토 열기 (${picked.size}개 파일)`}
          </button>
          {/* 선택값만 넘긴다 — 분석 화면에서 대상 확인·실행을 따로 눌러야 계산한다 */}
          <button
            type="button"
            className="mapping-confirm"
            disabled={picked.size === 0}
            onClick={() => navigate(`/analysis?files=${[...picked].join(',')}`)}
          >
            선택한 파일로 분석
          </button>
        </div>
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
          {/* 적용 대상: 검토하는 동안 어느 파일들을 모았는지 계속 보이게 한다 (파일 선택을 바꿔도 다시 열기 전에는 그대로) */}
          <div className="wk-scope" role="region" aria-label="적용 대상">
            <Icon name="file" />
            <span>
              <strong>적용 대상</strong> 공통 검토를 연 파일 {scope.length}개:{' '}
              <span className="wk-scope-names">{scope.map((id) => `#${id} ${nameOf(id)}`).join(', ')}</span>
            </span>
            {[...picked].sort().join(',') !== [...scope].sort().join(',') && (
              <span className="mapping-warn wk-scope-note">파일 선택이 바뀌었습니다 — '공통 검토 열기'를 다시 눌러야 반영됩니다.</span>
            )}
          </div>
          <StatusCards
            label="검토 구분 (누르면 그 구분만 보기)"
            picked={category}
            onPick={(key) => {
              setCategory(key as CommonCategory)
              setGroupId(null)
            }}
            cards={ORDER.map((c) => ({
              key: c,
              label: CATEGORY[c].label,
              count: counts[c] ?? 0,
              tone: CATEGORY_LOOK[c].tone,
              icon: CATEGORY_LOOK[c].icon,
            }))}
          />
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
                    <strong className="mw-colname">
                      <Icon name={CATEGORY_LOOK[g.category].icon} /> {g.name}
                    </strong>
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
                scopeCount={scope.length}
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
  scopeCount,
  fields,
  onDone,
  onClose,
}: {
  group: CommonGroup
  scopeCount: number
  fields: StandardFieldInfo[]
  onDone: (message: string) => void
  onClose: () => void
}) {
  // 기본 선택: 공통 후보 묶음에서 바꿀 수 있고 아직 확정하지 않은 열만. 다른 구분은 사용자가 직접 고른다
  const [chosen, setChosen] = useState<Set<string>>(
    () =>
      new Set(
        // '확인 필요'는 기본으로 고르지 않는다 (열마다 직접 골라야 포함)
        group.category === 'common'
          ? group.members.filter((m) => m.can_change && !m.confirmed && m.status === 'auto').map(memberKey)
          : [],
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
      <p className="wk-detail-state">
        <span className={`wk-badge tone-${CATEGORY_LOOK[group.category].tone}`}>
          <Icon name={CATEGORY_LOOK[group.category].icon} /> {CATEGORY[group.category].label}
        </span>
        <span>
          이 묶음: 파일 {group.files}개 · 열 {group.members.length}개 (공통 검토한 파일 {scopeCount}개 중)
        </span>
      </p>
      {group.evidence.length > 0 && (
        <div className="mw-evidence">
          <strong>묶음의 근거 (확인한 것·확인하지 못한 것)</strong>
          <ul>
            {group.evidence.map((e) => (
              <li key={e}>{e}</li>
            ))}
          </ul>
        </div>
      )}
      {group.reasons.length > 0 && (
        <Banner tone="warn" icon="alert" title="파일별 예외·주의">
          <ul className="mw-problems">
            {group.reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </Banner>
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
                        href={`/mapping?file=${m.file_id}&sheet=${m.sheet_id}&col=${m.column_id}`}
                        onClick={(e) => {
                          e.preventDefault()
                          navigate(`/mapping?file=${m.file_id}&sheet=${m.sheet_id}&col=${m.column_id}`)
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
                      {m.reason && !m.confirmed && <span className="mw-muted mw-block">제안 근거: {m.reason}</span>}
                    </div>
                  </td>
                  <td data-label="값 예시" className="mw-sample-cell">
                    <div className="mw-cell">{m.samples.map(formatCell).join(', ') || '-'}</div>
                  </td>
                  <td data-label="값 형태 · 단위">
                    <div className="mw-cell">
                      {VALUE_KIND_LABEL[m.value_kind] ?? m.value_kind}
                      {m.quantity && (
                        <span className="mw-block">
                          단위{' '}
                          {m.unit_state === 'one'
                            ? m.unit
                            : m.unit_state === 'ambiguous'
                              ? `불명확 (${m.unit_note ?? ''})`
                              : '표기 없음'}
                          {' · '}
                          {m.basis ? BASIS_TEXT[m.basis] : '흐름/시점 표기 없음'}
                        </span>
                      )}
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
                  sheet_id: m.sheet_id,
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
          title={`'${group.name}' 선택한 열 일괄 확정 확인`}
          note="이 묶음에서 고른 열만 대상입니다. 확인 표에서 열마다 뺄 수 있고, 기존 사용자 확정은 '기존 확정을 바꿈'을 골라야 바뀝니다."
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
