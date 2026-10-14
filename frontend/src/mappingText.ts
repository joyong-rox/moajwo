import { fetchFields, type Cell, type MappingMethod, type MappingStatus, type StandardFieldInfo } from './api'

/** 상태 이름: '자동 제안'(아직 확정 전)과 '사용자 확정'을 구분한다 */
export const STATUS_TEXT: Record<MappingStatus, string> = {
  confirmed: '사용자 확정',
  auto: '자동 제안',
  review: '확인 필요',
  unresolved: '미해결',
  unanalyzed: '미분석',
}

export const STATUS_HELP: Record<MappingStatus, string> = {
  confirmed: '사용자가 확정한 매핑입니다. 통합에 그대로 쓰입니다.',
  auto: '규칙·사전이 제안한 매핑입니다. 아직 확정 전이며, 사전 검토에서 자동 적용 항목으로 보입니다.',
  review: '자동으로 정하지 못해 사람이 확인해야 하는 제안입니다.',
  unresolved: '알맞은 표준 항목을 찾지 못했습니다.',
  unanalyzed: "아직 '열 매핑 분석'을 하지 않은 열입니다 (저장된 확정값이 있으면 사용자 확정으로 보입니다).",
}

export const METHOD_LABEL: Record<MappingMethod, string> = {
  exact: '표준 사전과 이름 일치',
  standard: '전사 표준 사전',
  learned: '내가 전에 확정한 이름',
  exact_part: '이름 일부가 사전과 일치',
  similarity: '이름·의미 유사도',
  pattern: '값 형식 규칙',
  llm: 'LLM 제안',
  user: '사용자 확정',
  none: '-',
}

/** '해당 없음'으로 확정 (표준 항목이 없는 열) */
export const NONE = '__none__'

export const NONE_HELP =
  "'해당 없음'으로 확정한 열은 통합 결과에 넣지 않습니다. 통합할 때 '표준 항목이 없는 열도 원래 이름으로 포함'을 고르면 원래 열 이름으로 들어갑니다. 확정하지 않은 열 중 '확인 필요'·'미해결'은 사전 검토에서 확인을 요청하고, '자동 제안'은 사전 검토의 자동 적용 항목으로 들어갑니다."

export const SCORE_HELP = '점수는 이름·의미의 유사도 참고값이며, 정답일 확률이 아닙니다.'

let fieldsPromise: Promise<StandardFieldInfo[]> | null = null
export function loadFields(): Promise<StandardFieldInfo[]> {
  fieldsPromise ??= fetchFields().catch((err: unknown) => {
    fieldsPromise = null
    throw err
  })
  return fieldsPromise
}

export function formatCell(value: Cell): string {
  if (value === null) return ''
  if (typeof value === 'number') return value.toLocaleString('ko-KR')
  return String(value)
}

export const VALUE_KIND_LABEL: Record<string, string> = {
  number: '숫자',
  code: '코드·번호',
  date: '날짜·기간',
  text: '글자',
  mixed: '섞임',
  empty: '값 없음',
}
