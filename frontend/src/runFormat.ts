import type { IntegrationRun } from './api'

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
