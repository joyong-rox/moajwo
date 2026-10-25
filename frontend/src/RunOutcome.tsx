import type { IntegrationRun } from './api'
import { OUTCOME_CLASS, runOutcome } from './runFormat'

/** 결과 파일이 없는 실행의 상태 설명 (검토 대기·만드는 중·결과 확인 필요·실패). 결과가 있으면 fallback. */
export function RunOutcomeText({ run, fallback = '-' }: { run: IntegrationRun; fallback?: string }) {
  const outcome = runOutcome(run)
  return outcome ? <span className={OUTCOME_CLASS[outcome.tone]}>{outcome.text}</span> : <>{fallback}</>
}
