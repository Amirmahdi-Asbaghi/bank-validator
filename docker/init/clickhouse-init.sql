CREATE TABLE IF NOT EXISTS validation_summary (
    run_id          UUID,
    bank_code       String,
    period          String,
    total_records   UInt32,
    valid_count     UInt32,
    invalid_count   UInt32,
    duplicate_count UInt32,
    errors_by_code  Map(String, UInt32),
    created_at      DateTime DEFAULT now(),
    finished_at     Nullable(DateTime)
) ENGINE = MergeTree()
ORDER BY (bank_code, period, created_at)
SETTINGS index_granularity = 8192;