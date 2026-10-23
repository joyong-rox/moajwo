# PostgreSQL 실행에서 빠졌던 169개 시험의 이번 처리 (2026-10-23)

예전 규칙: 파일 안에 `sqlite3.connect(` 문자열이 있으면 그 파일의 시험을 모두 PostgreSQL 실행에서 건너뜀 (최종 후보 `e2cb281`, run 39917375554 — 553 통과 · 169 건너뜀).
이번: 직접 연결을 앱과 같은 DB에 붙는 공통 도우미로 바꾸고, SQLite 자체만 보는 시험에만 `sqlite_only(이유)`를 붙임.
결과 열은 최종 후보 `9c3842b`의 격리 러너 PostgreSQL 17 실행(run 39937719132 — 761 통과 · 15 건너뜀)의 노드별 결과입니다.
시험 수의 비율은 코드 커버리지가 아닙니다.

| 파일 | 노드 수 |
|---|---|
| `tests/test_analysis_output_masking.py` | 12 |
| `tests/test_automation.py` | 5 |
| `tests/test_bundle.py` | 21 |
| `tests/test_crosscheck.py` | 61 |
| `tests/test_files_api.py` | 7 |
| `tests/test_integration.py` | 9 |
| `tests/test_llm.py` | 9 |
| `tests/test_mapping_review_fixes.py` | 9 |
| `tests/test_mapping_workspace.py` | 13 |
| `tests/test_migrations.py` | 13 |
| `tests/test_review.py` | 5 |
| `tests/test_source_columns.py` | 5 |

이번 결과: 건너뜀 13, 통과 156 (합계 169)

| 노드 | 이번 처리 | PostgreSQL 결과 | PostgreSQL 대응 시험 |
|---|---|---|---|
| `tests/test_analysis_output_masking.py::test_stored_past_result_really_contains_raw_text` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_analysis_output_masking.py::test_list_masks_summary_and_keeps_numbers` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_analysis_output_masking.py::test_detail_masks_conditions_inputs_meta_and_creator` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_analysis_output_masking.py::test_group_pages_mask_names_and_keep_colliding_groups` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_analysis_output_masking.py::test_row_pages_mask_names_and_keep_rows[excluded]` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_analysis_output_masking.py::test_row_pages_mask_names_and_keep_rows[detail]` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_analysis_output_masking.py::test_download_uses_the_same_masked_values_in_all_four_sheets` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_analysis_output_masking.py::test_permissions_unchanged_and_admin_sees_the_same_masking` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_analysis_output_masking.py::test_repeated_views_are_idempotent_without_recompute_parse_or_model` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_analysis_output_masking.py::test_normal_result_is_unchanged` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_analysis_output_masking.py::test_older_results_without_optional_fields_are_not_filled` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_analysis_output_masking.py::test_masking_does_not_touch_stored_copy` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_automation.py::test_saved_automation_skips_most_review` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_automation.py::test_automation_run_without_new_columns_completes` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_automation.py::test_automation_rejects_bad_requests` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_automation.py::test_automation_keeps_include_unmapped_choice` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_automation.py::test_screen_times_carry_kst_offset` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_bundle.py::test_four_files_without_summary_make_four_sheets_and_never_call_the_engine` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_bundle.py::test_four_files_with_summary_make_five_sheets_with_engine_values` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_bundle.py::test_only_selected_items_and_methods_appear` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_bundle.py::test_links_follow_confirmed_meaning_not_column_order_and_missing_links_must_be_excluded` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_bundle.py::test_unit_and_period_conflicts_follow_analysis_policy` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_bundle.py::test_duplicate_file_content_and_item_twice_are_blocked_for_summary` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_bundle.py::test_multi_sheet_file_needs_explicit_sheets_and_names_follow_excel_rules` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_bundle.py::test_values_are_preserved_and_formula_cells_without_cached_value_stay_blank` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_bundle.py::test_changes_after_plan_are_rejected_and_nothing_is_made` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_bundle.py::test_summary_failure_makes_no_file_and_asks_to_turn_it_off` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_bundle.py::test_other_users_files_and_pii_rules` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_bundle.py::test_limits_warn_and_never_truncate` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_bundle.py::test_runs_list_shows_bundle_mode_and_existing_requests_keep_old_behaviour` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_bundle.py::test_summary_keeps_blank_not_zero_and_upper_region_dedup` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_bundle.py::test_one_sheet_picked_from_multi_sheet_file_keeps_sheet_name` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_bundle.py::test_region_level_choice_upper_100_lower_90_is_recorded` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_bundle.py::test_variant_unit_and_repeated_row_choices_are_required_and_recorded` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_bundle.py::test_base_date_conflict_is_not_bypassed_by_region_level_choice` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_bundle.py::test_server_still_rejects_links_to_sheets_not_in_the_result` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_bundle.py::test_summary_conditions_never_return_or_store_raw_pii` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_bundle.py::test_summary_condition_choice_flags_keep_null_and_empty_variant_apart` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_year_columns_under_merged_header_are_kept` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_true_merge_copy_with_year_is_still_dropped` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_grand_total_error_not_hidden_by_first_row` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_above_subtotal_needs_complete_group` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_initial_owner_setup_is_atomic` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_column_names_masked_before_model_input` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_date_mapped_column_feeds_period_check[2026-08-True]` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_date_mapped_column_feeds_period_check[2026-09-False]` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_date_mapped_column_feeds_period_check[2026\ub144 8\uc6d4-True]` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_date_mapped_column_feeds_period_check[2026-08-31-True]` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_date_mapped_column_feeds_period_check[2026.09.30-False]` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_invalid_dates_and_codes_are_not_dates` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_monthly_flow_total_is_checked` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_stock_series_needs_evidence_and_records_it` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_broad_item_names_do_not_exempt_monthly_flow[\uc804\uc785 \uc778\uad6c]` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_broad_item_names_do_not_exempt_monthly_flow[\uc778\uad6c \uc774\ub3d9]` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_broad_item_names_do_not_exempt_monthly_flow[\ubcf4\uc720 \ucc28\ub7c9 \uc99d\uac10]` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_stock_field_with_total_unlike_any_period_is_checked` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_lone_total_column_equal_to_last_month_is_checked` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_future_entries_not_valid_without_reference_date` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_reference_period_requires_every_file` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_header_at_search_boundary[99]` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_header_at_search_boundary[100]` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_error_message_never_contains_key_fragment[0]` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_error_message_never_contains_key_fragment[150]` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_error_message_never_contains_key_fragment[190]` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_error_message_never_contains_key_fragment[199]` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_error_message_never_contains_key_fragment[400]` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_pii_column_names_are_not_learned_or_promoted` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_header_search_range_edges` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_url_error_and_auth_header_redacted_without_known_secret` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_above_subtotal_with_hidden_value_is_not_accepted` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_future_entries_are_candidates_not_keys` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_one_date_rule_for_mapping_period_and_glossary[2026-08]` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_one_date_rule_for_mapping_period_and_glossary[2026\ub144 8\uc6d4]` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_one_date_rule_for_mapping_period_and_glossary[2026.08.31]` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_one_date_rule_for_mapping_period_and_glossary[2026\ub144 8\uc6d4 31\uc77c]` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_one_date_rule_for_mapping_period_and_glossary[2026\ub144]` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_one_date_rule_for_mapping_period_and_glossary[2026-13]` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_one_date_rule_for_mapping_period_and_glossary[2026-02-30]` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_one_date_rule_for_mapping_period_and_glossary[\uc11c\uc6b8]` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_one_date_rule_for_mapping_period_and_glossary[1\uc6d4]` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_header_search_scope_is_bounded` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_initial_owner_race_across_processes` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 (여러 프로세스 최초 설정은 시험마다 새 PostgreSQL DB) | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_mapping_logs_do_not_contain_pii_column_names` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_count_total_equal_to_last_month_is_still_checked` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_flow_totals_equal_to_last_month_are_checked[\uc9d1\ud589\uc561]` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_flow_totals_equal_to_last_month_are_checked[\uc804\uc785 \uc778\uad6c]` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_stock_uses_latest_period_not_physical_last_row` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_mixed_period_labels_are_not_guessed` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_mixed_nature_columns_are_judged_per_column` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_kosis_vehicle_table_with_total_column` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_title_wording_is_independent_evidence` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_alias_api_and_learning_never_store_pii_names` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_file_name_evidence_does_not_cover_flow_column` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_flow_wording_beats_stock_mapping[\uc2e0\uaddc \uc2b9\uc6a9\ucc28-passenger_car]` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_flow_wording_beats_stock_mapping[\uc804\uc785 \uc778\uad6c-population]` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_flow_wording_beats_stock_mapping[\uc99d\uac10 \uc138\ub300\uc218-households]` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_title_cumulative_word_with_monthly_flow_column` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_duplicate_latest_period_is_ambiguous` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_crosscheck.py::test_reverse_order_with_title_evidence_single_column` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_files_api.py::test_upload_multiple` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_files_api.py::test_original_saved_read_only_with_new_name` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_files_api.py::test_mapping_endpoint` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_files_api.py::test_decisions_saved_and_learned` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_files_api.py::test_corrupt_file_reports_error` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_files_api.py::test_xlsx_that_expands_beyond_the_limit_is_not_read` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_files_api.py::test_validation_flow_blocks_and_pii` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_integration.py::test_integration_creates_three_sheets_with_painted_changes` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_integration.py::test_integration_requires_completed_review` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_integration.py::test_missing_change_record_fails_without_output` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_integration.py::test_review_preview_matches_integration` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_integration.py::test_unmapped_columns_kept_with_original_names_when_asked` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_integration.py::test_blank_csv_cells_are_not_changes` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_integration.py::test_sigungu_full_name_kept_without_sido_column` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_integration.py::test_unchecked_sums_are_listed_in_the_result_summary` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_integration.py::test_text_cells_that_look_like_formulas_stay_text` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_llm.py::test_default_is_local_ollama` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_llm.py::test_only_owner_can_change` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_llm.py::test_save_external_mode_without_leaking_key` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_llm.py::test_custom_endpoint_requires_url` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_llm.py::test_anthropic_request_shape` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_llm.py::test_openai_and_custom_request_shape` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_llm.py::test_errors_never_contain_key` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_llm.py::test_connection_test_endpoint` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_llm.py::test_engine_uses_selected_provider` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_mapping_review_fixes.py::test_flow_and_stock_wording_conflict_is_not_common` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_mapping_review_fixes.py::test_quantity_stored_as_text_still_compares_units` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_mapping_review_fixes.py::test_multiple_unit_declarations_are_ambiguous` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_mapping_review_fixes.py::test_common_candidate_lists_checked_evidence` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_mapping_review_fixes.py::test_pii_in_title_sheet_and_file_name_not_in_review_or_batch` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_mapping_review_fixes.py::test_decisions_api_finds_pii_sheet_by_sheet_id` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_mapping_review_fixes.py::test_batch_plan_shows_status_and_reason` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_mapping_review_fixes.py::test_latest_without_analysis_shows_saved_decisions_without_writing` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_mapping_review_fixes.py::test_concurrent_first_analysis_keeps_one_row` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_mapping_workspace.py::test_preview_letters_duplicates_paging_and_permissions` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_mapping_workspace.py::test_preview_masks_headers_cells_and_numeric_rrn` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_mapping_workspace.py::test_latest_mapping_is_stored_and_merges_decisions` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_mapping_workspace.py::test_common_review_groups_without_reanalysis` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_mapping_workspace.py::test_common_review_rejects_unviewable_files` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_mapping_workspace.py::test_batch_preview_and_apply_with_overwrite_and_idempotency` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_mapping_workspace.py::test_batch_apply_is_all_or_nothing` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_mapping_workspace.py::test_concurrent_batch_applies_one_wins` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_mapping_workspace.py::test_common_members_match_single_file_results` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_mapping_workspace.py::test_preview_view_cache_reuses_masked_parse_and_stays_small` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_mapping_workspace.py::test_view_cache_keeps_many_small_files_within_a_size_budget` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_mapping_workspace.py::test_view_cache_size_counts_long_text` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_mapping_workspace.py::test_concurrent_previews_parse_once` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_migrations.py::test_base_sido_entries_seeded_and_removed` | SQLite 전용 유지 — 빈 SQLite 파일에 alembic을 실행해 그 파일을 읽음 (예전 리비전 자료·downgrade) | 건너뜀 | `test_migrations_postgres.py` — 빈 PostgreSQL → head, 모델과 차이 0, 기본 용어사전 자료가 SQLite head와 같음, 기동 때 잠금·응답 없는 서버에서 제한 시간 뒤 실패 |
| `tests/test_migrations.py::test_reorganization_entries_and_successor_aliases` | SQLite 전용 유지 — 빈 SQLite 파일에 alembic을 실행해 그 파일을 읽음 (예전 리비전 자료·downgrade) | 건너뜀 | `test_migrations_postgres.py` — 빈 PostgreSQL → head, 모델과 차이 0, 기본 용어사전 자료가 SQLite head와 같음, 기동 때 잠금·응답 없는 서버에서 제한 시간 뒤 실패 |
| `tests/test_migrations.py::test_general_gu_entries` | SQLite 전용 유지 — 빈 SQLite 파일에 alembic을 실행해 그 파일을 읽음 (예전 리비전 자료·downgrade) | 건너뜀 | `test_migrations_postgres.py` — 빈 PostgreSQL → head, 모델과 차이 0, 기본 용어사전 자료가 SQLite head와 같음, 기동 때 잠금·응답 없는 서버에서 제한 시간 뒤 실패 |
| `tests/test_migrations.py::test_direction_gu_are_ambiguous_and_sejong_duplicates_resolve` | SQLite 전용 유지 — 빈 SQLite 파일에 alembic을 실행해 그 파일을 읽음 (예전 리비전 자료·downgrade) | 건너뜀 | `test_migrations_postgres.py` — 빈 PostgreSQL → head, 모델과 차이 0, 기본 용어사전 자료가 SQLite head와 같음, 기동 때 잠금·응답 없는 서버에서 제한 시간 뒤 실패 |
| `tests/test_migrations.py::test_0011_fills_only_empty_region_fields` | SQLite 전용 유지 — 빈 SQLite 파일에 alembic을 실행해 그 파일을 읽음 (예전 리비전 자료·downgrade) | 건너뜀 | `test_migrations_postgres.py` — 빈 PostgreSQL → head, 모델과 차이 0, 기본 용어사전 자료가 SQLite head와 같음, 기동 때 잠금·응답 없는 서버에서 제한 시간 뒤 실패 |
| `tests/test_migrations.py::test_downgrade_keeps_entries_users_refer_to` | SQLite 전용 유지 — 빈 SQLite 파일에 alembic을 실행해 그 파일을 읽음 (예전 리비전 자료·downgrade) | 건너뜀 | `test_migrations_postgres.py` — 빈 PostgreSQL → head, 모델과 차이 0, 기본 용어사전 자료가 SQLite head와 같음, 기동 때 잠금·응답 없는 서버에서 제한 시간 뒤 실패 |
| `tests/test_migrations.py::test_0015_marks_existing_installs_and_keeps_new_ones_open` | SQLite 전용 유지 — 빈 SQLite 파일에 alembic을 실행해 그 파일을 읽음 (예전 리비전 자료·downgrade) | 건너뜀 | `test_migrations_postgres.py` — 빈 PostgreSQL → head, 모델과 차이 0, 기본 용어사전 자료가 SQLite head와 같음, 기동 때 잠금·응답 없는 서버에서 제한 시간 뒤 실패 |
| `tests/test_migrations.py::test_setup_stays_closed_after_0015_upgrade` | SQLite 전용 유지 — 빈 SQLite 파일에 alembic을 실행해 그 파일을 읽음 (예전 리비전 자료·downgrade) | 건너뜀 | `test_migrations_postgres.py` — 빈 PostgreSQL → head, 모델과 차이 0, 기본 용어사전 자료가 SQLite head와 같음, 기동 때 잠금·응답 없는 서버에서 제한 시간 뒤 실패 |
| `tests/test_migrations.py::test_kept_base_entries_do_not_duplicate_on_reupgrade` | SQLite 전용 유지 — 빈 SQLite 파일에 alembic을 실행해 그 파일을 읽음 (예전 리비전 자료·downgrade) | 건너뜀 | `test_migrations_postgres.py` — 빈 PostgreSQL → head, 모델과 차이 0, 기본 용어사전 자료가 SQLite head와 같음, 기동 때 잠금·응답 없는 서버에서 제한 시간 뒤 실패 |
| `tests/test_migrations.py::test_0016_converts_existing_accounts_and_downgrade_restores_roles` | SQLite 전용 유지 — 빈 SQLite 파일에 alembic을 실행해 그 파일을 읽음 (예전 리비전 자료·downgrade) | 건너뜀 | `test_migrations_postgres.py` — 빈 PostgreSQL → head, 모델과 차이 0, 기본 용어사전 자료가 SQLite head와 같음, 기동 때 잠금·응답 없는 서버에서 제한 시간 뒤 실패 |
| `tests/test_migrations.py::test_0017_keeps_existing_sessions_and_downgrades` | SQLite 전용 유지 — 빈 SQLite 파일에 alembic을 실행해 그 파일을 읽음 (예전 리비전 자료·downgrade) | 건너뜀 | `test_migrations_postgres.py` — 빈 PostgreSQL → head, 모델과 차이 0, 기본 용어사전 자료가 SQLite head와 같음, 기동 때 잠금·응답 없는 서버에서 제한 시간 뒤 실패 |
| `tests/test_migrations.py::test_0018_mapping_analyses_up_and_down` | SQLite 전용 유지 — 빈 SQLite 파일에 alembic을 실행해 그 파일을 읽음 (예전 리비전 자료·downgrade) | 건너뜀 | `test_migrations_postgres.py` — 빈 PostgreSQL → head, 모델과 차이 0, 기본 용어사전 자료가 SQLite head와 같음, 기동 때 잠금·응답 없는 서버에서 제한 시간 뒤 실패 |
| `tests/test_migrations.py::test_0019_analysis_runs_up_down_and_up_again` | SQLite 전용 유지 — 빈 SQLite 파일에 alembic을 실행해 그 파일을 읽음 (예전 리비전 자료·downgrade) | 건너뜀 | `test_migrations_postgres.py` — 빈 PostgreSQL → head, 모델과 차이 0, 기본 용어사전 자료가 SQLite head와 같음, 기동 때 잠금·응답 없는 서버에서 제한 시간 뒤 실패 |
| `tests/test_review.py::test_review_items_categories_and_reasons` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_review.py::test_decisions_are_persisted_to_backend_state` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_review.py::test_remember_format_learns_on_complete` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_review.py::test_recheck_carries_decisions` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_review.py::test_recheck_with_fixed_file` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_source_columns.py::test_analysis_names_the_source_column_everywhere` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_source_columns.py::test_different_source_names_are_all_listed_with_a_check_note` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_source_columns.py::test_source_names_that_mask_the_same_are_numbered_without_raw_text` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_source_columns.py::test_results_saved_before_the_list_show_columns_from_their_inputs` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
| `tests/test_source_columns.py::test_bundle_summary_names_the_source_column_in_plan_excel_and_run` | 앱과 같은 DB로 조회하도록 바꿔 PostgreSQL에서도 실행 | 통과 | 같은 시험이 직접 실행 |
