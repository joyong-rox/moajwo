// 진행 상태 막대의 단계 문구와 예상 시간 (Progress.tsx에서 사용)

/** 진행 단계: label = 화면 문구, ms = 이 단계에 보통 걸리는 시간(예상치) */
export type Stage = { label: string; ms: number }

export const REVIEW_STAGES: Stage[] = [
  { label: '파일 파싱 중… (헤더·병합 셀·숫자 변환)', ms: 1500 },
  { label: 'AI 매핑 진행 중… (사전 비교 → 유사도 → LLM 판정)', ms: 9000 },
  { label: '데이터 검증 중… (합계·기간·필수 기관·이상치)', ms: 2500 },
  { label: '검토 항목 정리 중…', ms: 1200 },
]

export const UPLOAD_STAGES: Stage[] = [
  { label: '파일 업로드 중…', ms: 1000 },
  { label: '파일 파싱 중… (헤더·병합 셀·숫자 변환)', ms: 2000 },
  { label: '개인정보 패턴 확인 중…', ms: 800 },
]

export const MAPPING_STAGES: Stage[] = [
  { label: '표준 사전과 열 이름 비교 중…', ms: 1200 },
  { label: 'AI 매핑 진행 중… (유사도 → LLM 판정)', ms: 9000 },
]
