import { useId, useMemo, useState } from 'react'
import type { StandardFieldInfo } from './api'
import { NONE } from './mappingText'

function matches(f: StandardFieldInfo, q: string): boolean {
  if (!q) return true
  const t = q.replace(/\s+/g, '').toLowerCase()
  return [f.label, f.key, f.description, ...(f.aliases ?? [])].some((s) =>
    s.replace(/\s+/g, '').toLowerCase().includes(t),
  )
}

/** 표준 항목 검색·선택 (키보드: 검색 입력 → Tab으로 목록 → 화살표·Space로 선택) */
function FieldPicker({
  fields,
  value,
  onChange,
  disabled,
  suggested,
  label = '표준 항목 선택',
}: {
  fields: StandardFieldInfo[]
  value: string
  onChange: (value: string) => void
  disabled?: boolean
  suggested?: string[]
  label?: string
}) {
  const [query, setQuery] = useState('')
  const name = useId()
  const shown = useMemo(() => {
    const hit = fields.filter((f) => matches(f, query))
    const first = new Set(suggested ?? [])
    return [...hit.filter((f) => first.has(f.key)), ...hit.filter((f) => !first.has(f.key))]
  }, [fields, query, suggested])
  const chosen = fields.find((f) => f.key === value)
  return (
    <fieldset className="mw-picker" disabled={disabled}>
      <legend>{label}</legend>
      <input
        type="search"
        className="mw-picker-search"
        placeholder="이름·설명·다른 이름으로 검색"
        aria-label={`${label} 검색`}
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      <div className="mw-picker-list" role="radiogroup" aria-label={label}>
        <label className={`mw-picker-option${value === NONE ? ' is-chosen' : ''}`}>
          <input type="radio" name={name} checked={value === NONE} onChange={() => onChange(NONE)} />
          <span>
            <strong>해당 없음</strong>
            <span className="mw-muted"> · 표준 항목이 없는 열</span>
          </span>
        </label>
        {shown.map((f) => (
          <label key={f.key} className={`mw-picker-option${value === f.key ? ' is-chosen' : ''}`}>
            <input type="radio" name={name} checked={value === f.key} onChange={() => onChange(f.key)} />
            <span>
              <strong>{f.label}</strong>
              {suggested?.includes(f.key) && <span className="upload-tag mapping-review">후보</span>}
              {f.description && <span className="mw-muted"> · {f.description}</span>}
            </span>
          </label>
        ))}
        {shown.length === 0 && <p className="mw-muted">검색 결과가 없습니다.</p>}
      </div>
      {chosen && (
        <p className="mw-field-def">
          <strong>{chosen.label}</strong>: {chosen.description || '사전에 설명이 없습니다.'}
          {chosen.aliases && chosen.aliases.length > 0 && (
            <span className="mw-muted"> · 사전의 다른 이름: {chosen.aliases.slice(0, 8).join(', ')}</span>
          )}
        </p>
      )}
    </fieldset>
  )
}

export default FieldPicker
