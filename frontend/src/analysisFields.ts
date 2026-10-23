import type { SourceColumn } from './api'

/** 1차 분석 대상: 더해서 의미가 있는 수량 항목 (서버 analysis.MEASURE_FIELDS와 같음 — 메일 지표는 app.mail_fields).
 * 진입 버튼 표시에만 쓰고, 실제 허용 여부는 서버가 다시 판단한다. 서버 목록과 같은지는 backend 시험이 확인한다. */
export const ANALYSIS_FIELDS: ReadonlySet<string> = new Set([
  'population',
  'population_male',
  'population_female',
  'households',
  'headcount',
  'budget',
  'expenditure',
  'amount',
  'count',
  'registration_count',
  'vehicles_total',
  'passenger_car',
  'van',
  'truck',
  'special_vehicle',
  'motorcycle',
  'eco_friendly_car',
  'electric_car',
  'hydrogen_car',
  'hybrid_car',
  'mail_sent_count',
  'mail_external_sent_count',
  'mail_sent_total_count',
  'mail_received_count',
  'mail_external_received_count',
  'mail_received_total_count',
  'mail_read_count',
  'mail_mobile_sent_count',
  'mail_mobile_read_count',
  'mail_large_file_sent_count',
  'mail_system_sent_count',
  'mailbox_count',
  'mail_all_sent_total_count',
  'mail_sent_size_mb',
  'mail_received_size_mb',
  'mailbox_size_mb',
  'mail_archive_size_mb',
])

/** 계산한 원본 열을 한 줄로 ('발신Mail건수 (시트 5개)'). 표준 항목 이름만으로는 어느 지표인지 알 수 없어 함께 보인다 */
export function sourceText(cols: SourceColumn[]): string {
  return cols.map((c) => `${c.column} (시트 ${c.sheets}개)`).join(' · ')
}

/** 원본 열 이름이 둘 이상일 때 붙이는 확인 안내 (서버 Excel의 안내와 같은 뜻) */
export const SOURCE_NAMES_DIFFER =
  '이름이 서로 다른 열을 같은 표준 항목으로 확정했습니다 — 같은 지표인지 파일·시트·열로 확인하세요.'
