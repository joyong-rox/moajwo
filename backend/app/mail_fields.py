"""메일 통계의 계산 범위. 조직 식별자와 서로 다른 발신 합계는 구분한다."""

MAIL_MB_FIELDS = frozenset(
    {
        "mail_sent_size_mb",
        "mail_received_size_mb",
        "mailbox_size_mb",
        "mail_archive_size_mb",
    }
)
MAIL_STOCK_FIELDS = frozenset({"mailbox_count", "mailbox_size_mb", "mail_archive_size_mb"})
MAIL_MEASURE_FIELDS = (
    frozenset(
        {
            "mail_sent_count",
            "mail_external_sent_count",
            "mail_sent_total_count",
            "mail_received_count",
            "mail_external_received_count",
            "mail_received_total_count",
            "mail_read_count",
            "mail_mobile_sent_count",
            "mail_mobile_read_count",
            "mail_large_file_sent_count",
            "mail_system_sent_count",
            "mailbox_count",
            "mail_all_sent_total_count",
        }
    )
    | MAIL_MB_FIELDS
)
