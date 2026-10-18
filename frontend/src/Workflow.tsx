import type { ReactNode } from 'react'
import { navigate } from './router'

/** 작업 화면 공용 부품: 단계 안내 · 상태 카드 · 안내 띠 · 아이콘. 상태는 색만으로 구별하지 않고 아이콘과 문구를 함께 쓴다. */

export type IconName =
  | 'check'
  | 'alert'
  | 'block'
  | 'info'
  | 'recheck'
  | 'lock'
  | 'clock'
  | 'file'
  | 'user'
  | 'none'
  | 'question'
  | 'link'
  | 'one'
  | 'list'
  | 'table'

const PATHS: Record<IconName, ReactNode> = {
  check: <path d="M3.5 8.5l3 3 6-7" />,
  alert: (
    <>
      <path d="M8 2.5l6 11H2z" />
      <path d="M8 6.5v3.2M8 11.6v.4" />
    </>
  ),
  block: (
    <>
      <circle cx="8" cy="8" r="5.6" />
      <path d="M4.1 11.9l7.8-7.8" />
    </>
  ),
  info: (
    <>
      <circle cx="8" cy="8" r="5.6" />
      <path d="M8 7.2v4M8 4.8v.4" />
    </>
  ),
  recheck: (
    <>
      <path d="M12.8 6.2A5 5 0 0 0 3.6 5.4M3.2 9.8a5 5 0 0 0 9.2.8" />
      <path d="M3.4 2.8v2.8h2.8M12.6 13.2v-2.8H9.8" />
    </>
  ),
  lock: (
    <>
      <rect x="3.5" y="7" width="9" height="6.5" rx="1.4" />
      <path d="M5.5 7V5.2a2.5 2.5 0 0 1 5 0V7" />
    </>
  ),
  clock: (
    <>
      <circle cx="8" cy="8" r="5.6" />
      <path d="M8 4.8V8l2.2 1.4" />
    </>
  ),
  file: (
    <>
      <path d="M4 2h5l3 3v9H4z" />
      <path d="M9 2v3h3" />
    </>
  ),
  user: (
    <>
      <circle cx="8" cy="5.5" r="2.5" />
      <path d="M3.2 13.5a4.8 4.8 0 0 1 9.6 0" />
    </>
  ),
  none: (
    <>
      <circle cx="8" cy="8" r="5.6" />
      <path d="M5.2 8h5.6" />
    </>
  ),
  question: (
    <>
      <circle cx="8" cy="8" r="5.6" />
      <path d="M6.3 6.4a1.8 1.8 0 1 1 2.4 1.7c-.5.2-.7.6-.7 1.1v.3M8 11.4v.4" />
    </>
  ),
  link: (
    <>
      <path d="M6.6 9.4l2.8-2.8" />
      <path d="M7.4 4.8l1-1a2.6 2.6 0 0 1 3.7 3.7l-1 1M8.6 11.2l-1 1a2.6 2.6 0 0 1-3.7-3.7l1-1" />
    </>
  ),
  one: (
    <>
      <rect x="2.5" y="2.5" width="11" height="11" rx="2" />
      <path d="M7 5.6l1.3-.8v6.4M6.6 11.2h3.4" />
    </>
  ),
  list: <path d="M5.5 4.5h8M5.5 8h8M5.5 11.5h8M2.6 4.5h.4M2.6 8h.4M2.6 11.5h.4" />,
  table: (
    <>
      <rect x="2.5" y="3" width="11" height="10" rx="1.2" />
      <path d="M2.5 6.5h11M6.5 6.5V13" />
    </>
  ),
}

export function Icon({ name, label }: { name: IconName; label?: string }) {
  return (
    <svg
      className="wk-icon"
      viewBox="0 0 16 16"
      width="16"
      height="16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      role={label ? 'img' : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      focusable="false"
    >
      {PATHS[name]}
    </svg>
  )
}

// ---------------------------------------------------------------- 단계 안내

/** done: 서버 상태로 끝난 단계 · attention: 처리할 것이 남음 · todo: 아직 · (current는 지금 보고 있는 단계 — 완료와 별개) */
export type StepState = 'done' | 'attention' | 'todo'
export type Step = {
  key: 'files' | 'mapping' | 'review' | 'result'
  state: StepState
  detail?: string // 예: '확인 필요 3 · 미분석 1'
  href?: string // 그 단계로 가는 기존 주소
  current?: boolean
}

const STEP_LABEL: Record<Step['key'], string> = {
  files: '파일 선택',
  mapping: '열 매핑',
  review: '검사·검토',
  result: '결과',
}
const STATE_TEXT: Record<StepState, string> = { done: '완료', attention: '확인할 것 있음', todo: '아직' }
const STATE_ICON: Record<StepState, IconName> = { done: 'check', attention: 'alert', todo: 'clock' }

function go(href: string) {
  return (e: React.MouseEvent<HTMLAnchorElement>) => {
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
    e.preventDefault()
    navigate(href)
  }
}

/** 파일 선택 → 열 매핑 → 검사·검토 → 결과. 완료 여부는 각 화면이 서버 상태로 정해 넘긴다 (방문만으로 완료 처리하지 않음). */
export function WorkflowSteps({ steps, label = '작업 단계' }: { steps: Step[]; label?: string }) {
  return (
    <nav className="wk-steps" aria-label={label}>
      <ol>
        {steps.map((s, i) => {
          const body = (
            <>
              <span className={`wk-step-mark is-${s.state}`}>
                <Icon name={STATE_ICON[s.state]} />
              </span>
              <span className="wk-step-text">
                <span className="wk-step-name">
                  {i + 1}. {STEP_LABEL[s.key]}
                </span>
                <span className="wk-step-state">
                  {STATE_TEXT[s.state]}
                  {s.detail ? ` · ${s.detail}` : ''}
                </span>
              </span>
            </>
          )
          return (
            <li key={s.key} className={`wk-step is-${s.state}${s.current ? ' is-current' : ''}`}>
              {s.href && !s.current ? (
                <a href={s.href} onClick={go(s.href)} className="wk-step-body">
                  {body}
                </a>
              ) : (
                <span className="wk-step-body" aria-current={s.current ? 'step' : undefined}>
                  {body}
                </span>
              )}
            </li>
          )
        })}
      </ol>
    </nav>
  )
}

// ---------------------------------------------------------------- 상태 카드

export type Tone = 'ok' | 'auto' | 'review' | 'unresolved' | 'block' | 'warn' | 'recheck' | 'muted' | 'info' | 'pii'
export type StatusCard = {
  key: string
  label: string
  count: number | string
  tone: Tone
  icon: IconName
  hint?: string
}

/** 상태별 건수 카드. onPick을 주면 필터 버튼(aria-pressed)으로 쓴다. */
export function StatusCards({
  cards,
  label,
  picked,
  onPick,
}: {
  cards: StatusCard[]
  label: string
  picked?: string
  onPick?: (key: string) => void
}) {
  return (
    <div className="wk-cards" role={onPick ? 'group' : 'list'} aria-label={label}>
      {cards.map((c) => {
        const inner = (
          <>
            <span className={`wk-card-icon tone-${c.tone}`}>
              <Icon name={c.icon} />
            </span>
            <span className="wk-card-count">{c.count}</span>
            <span className="wk-card-label">{c.label}</span>
            {c.hint && <span className="wk-card-hint">{c.hint}</span>}
          </>
        )
        return onPick ? (
          <button
            key={c.key}
            type="button"
            className={`wk-card${picked === c.key ? ' is-picked' : ''}`}
            aria-pressed={picked === c.key}
            onClick={() => onPick(c.key)}
          >
            {inner}
          </button>
        ) : (
          <div key={c.key} className="wk-card" role="listitem">
            {inner}
          </div>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------- 안내 띠

export function Banner({
  tone,
  icon,
  title,
  children,
  actions,
  live,
}: {
  tone: Tone
  icon: IconName
  title: string
  children?: ReactNode
  actions?: ReactNode
  live?: 'status' | 'alert'
}) {
  return (
    <div className={`wk-banner tone-${tone}`} role={live}>
      <span className="wk-banner-icon">
        <Icon name={icon} />
      </span>
      <div className="wk-banner-body">
        <strong className="wk-banner-title">{title}</strong>
        {children}
      </div>
      {actions && <div className="wk-banner-actions">{actions}</div>}
    </div>
  )
}

/** 기존 주소로 이동하는 링크 (새 탭 열기는 브라우저에 맡김) */
export function NavLink({ href, className, children }: { href: string; className?: string; children: ReactNode }) {
  return (
    <a href={href} className={className} onClick={go(href)}>
      {children}
    </a>
  )
}
