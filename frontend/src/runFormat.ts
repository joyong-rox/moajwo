import type { IntegrationRun, KeyJoinScope } from './api'

const OFFSET = /(Z|[+-]\d{2}:?\d{2})$/i
const KST_PARTS = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Asia/Seoul',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hourCycle: 'h23',
})

/** 서버 시각 → 한국 시간 'YYYY-MM-DD HH:mm:ss'. 시간대가 붙은 값은 그대로 읽고,
 *  시간대가 없는 값은 UTC로 본다 (서버는 화면용 시각에 +09:00을 붙여 보낸다). */
export function formatTime(value: string | null): string {
  if (!value) return '-'
  const iso = value.trim().replace(' ', 'T')
  const date = new Date(OFFSET.test(iso) ? iso : `${iso}Z`)
  if (Number.isNaN(date.getTime())) return value.replace('T', ' ').slice(0, 19)
  const p = Object.fromEntries(KST_PARTS.formatToParts(date).map((x) => [x.type, x.value]))
  return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}:${p.second}`
}

export function formatDuration(ms: number | null): string {
  if (ms === null) return '-'
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}초`
  const minutes = Math.floor(ms / 60_000)
  return `${minutes}분 ${Math.round((ms % 60_000) / 1000)}초`
}

export const STATUS_CLASS: Record<IntegrationRun['status'], string> = {
  done: 'is-done',
  review: 'is-review',
  failed: 'is-failed',
  running: 'is-running',
  check: 'is-check',
}

/** 결과 파일이 없는 실행의 짧은 설명과 색 (done은 화면마다 내려받기를 그린다). 상태를 더하면 tsc가 여기서 알린다. */
export function runOutcome(r: IntegrationRun): { text: string; tone: 'muted' | 'warn' | 'error' } | null {
  switch (r.status) {
    case 'done':
      return null
    case 'review':
      return { text: '검토 대기', tone: 'muted' }
    case 'running':
      return { text: '만드는 중 (끝나면 새로고침)', tone: 'muted' }
    case 'check':
      return { text: `결과 확인 필요 — ${r.error ?? '관리자 확인이 필요합니다.'}`, tone: 'warn' }
    case 'failed':
      return { text: r.error ?? '실패', tone: 'error' }
    default: {
      const unknown: never = r.status
      return unknown
    }
  }
}

export const OUTCOME_CLASS = { muted: 'mw-muted', warn: 'run-check', error: 'upload-error' } as const

// ---------------------------------------------------------------- 공통 key 연결 (summary.mode === 'keyjoin')
/** 실행 기록의 방식 열 표기 — '직접 검토'·'시트 묶기'와 같은 길이로 표 열 너비를 바꾸지 않는다 */
export const KEYJOIN_MODE_SHORT = 'key 취합'
export const KEYJOIN_MODE_TITLE = '공통 key로 연결하기 (검토 없음)'
/** 포함할 key 범위 문구 (연결 화면의 선택지·실행 기록 공용) — 연결 대상은 사람이 아닐 수 있어 '사용자'라 하지 않는다 */
export const KEYJOIN_SCOPE_LABEL: Record<KeyJoinScope, string> = {
  base: '기준 파일의 key만',
  all: '모든 파일의 key',
  common: '모든 파일에 공통인 key만',
}
export const KEYJOIN_SCOPE_SHORT: Record<KeyJoinScope, string> = { base: '기준', all: '모든', common: '공통' }

/** 실행 기록 요약의 입력 수 — 시작 요약은 개수, 완료 요약은 입력 목록 (없으면 null) */
export function keyJoinInputCount(summary: IntegrationRun['summary']): number | null {
  const v = summary.inputs
  if (Array.isArray(v)) return v.length
  return typeof v === 'number' ? v : null
}
