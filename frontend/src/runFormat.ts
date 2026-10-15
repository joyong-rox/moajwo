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
}
