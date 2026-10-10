import type { IntegrationRun } from './api'

/** 서버가 KST로 저장한 시각을 그대로 'YYYY-MM-DD HH:MM:SS'로 보여준다 (브라우저 시간대로 바꾸지 않음). */
export function formatTime(value: string | null): string {
  if (!value) return '-'
  return value.replace('T', ' ').slice(0, 19)
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
