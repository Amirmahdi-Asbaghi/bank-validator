"""Django models for the validation platform.

Four models, two roles:

    Reference
        BankReference   allow-list used by E004

    Operational state
        ValidationRun   one row per upload — the audit anchor
        ValidRecord     passing rows (sync path only)
        InvalidRecord   failing rows + reasons (sync path only)

Design notes:

    - UUIDs for primary keys. Non-guessable, distributed-safe, and
      shared across Postgres, ClickHouse, and Delta without collisions.

    - Decimal for money, never float. ``max_digits=18, decimal_places=2``
      covers values up to 10^16 with two decimal places — enough for
      any realistic bank amount.

    - JSONField for variable-shape data. ``errors_by_code`` is a map,
      ``raw_row`` is the untouched input. Both vary per record, so a
      relational schema would need extra tables for no benefit.

    - Records are only stored here for the sync path. Async runs write
      to Delta (curated/quarantine) on MinIO, not to these tables.
"""
import uuid

from django.db import models


class BankReference(models.Model):
    """Allow-list of valid bank codes.

    Seeded from ``data/reference/bank_codes.csv`` via the
    ``seed_bank_codes`` management command. The rule engine (E004)
    reads this once per batch and passes it through ``context``.

    Only ``is_active=True`` rows are used. Deactivating a bank does
    not delete historical runs — the reference is a filter, not a
    cascade.
    """
    bank_code = models.CharField(max_length=32, unique=True, db_index=True)
    name = models.CharField(max_length=128, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["bank_code"]

    def __str__(self):
        return f"{self.bank_code} — {self.name}"


class ValidationRun(models.Model):
    """One upload / validation run.

    This is the anchor for audit and idempotency. Every other record
    (ValidRecord, InvalidRecord) foreign-keys back to this row.

    For the sync path, all count fields are populated before the API
    returns. For the async path, this row stays at ``status="queued"``
    and the real counts live in ClickHouse (see ``reports/views.py``
    for the fallback logic).
    """

    class Status(models.TextChoices):
        """Lifecycle of a run.

        Sync:   QUEUED → RUNNING → COMPLETED (or FAILED), often too
                quickly to observe the intermediate states.
        Async:  QUEUED (written on upload) → stays queued in Postgres;
                the real completion lives in ClickHouse.
        """
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    # Primary key: UUID, generated client-side. Rationale:
    #   - Non-sequential → clients can't enumerate other runs
    #   - Distributed-safe  → Spark/ClickHouse can reference the same ID
    #   - Predictable       → the ID is known before the DB write
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Denormalized metadata for filtering. Currently not populated by
    # the ingestion service — a known limitation documented in
    # docs/decisions.md. The fields exist so that future filtering by
    # bank/period doesn't require a schema migration.
    bank_code = models.CharField(max_length=32, blank=True, db_index=True)
    period = models.CharField(max_length=16, blank=True, db_index=True)

    # SHA-256 of the raw file content. Used for idempotency detection:
    # the same bytes uploaded twice produce the same hash, so a future
    # improvement can short-circuit duplicate uploads.
    file_hash = models.CharField(max_length=64, db_index=True)

    # Where the raw file lives in object storage. For sync runs this
    # is the MinIO URI (``s3://raw/uploads/<uuid>.csv``); for early
    # uploads it was the original filename. max_length=512 covers
    # long S3 keys.
    source_file = models.CharField(max_length=512)
    file_size_bytes = models.BigIntegerField(default=0)

    # Run lifecycle state. Indexed because dashboards and health checks
    # query "how many runs are stuck in queued?".
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.QUEUED,
        db_index=True,
    )

    # Aggregate counts. Filled by the sync path immediately; filled by
    # the async path only if Spark writes back to Postgres (currently
    # it doesn't — the API reads ClickHouse instead).
    total_records = models.IntegerField(default=0)
    valid_count = models.IntegerField(default=0)
    invalid_count = models.IntegerField(default=0)
    duplicate_count = models.IntegerField(default=0)

    # Map of error_code → count, e.g. ``{"E004": 9034, "E007": 9800}``.
    # JSONField because the set of codes can grow and the shape is
    # naturally a dictionary.
    errors_by_code = models.JSONField(default=dict, blank=True)

    # Human-readable error for whole-run failures (e.g. "CSV is not
    # UTF-8"). Distinct from per-record errors, which live in
    # InvalidRecord.
    error_message = models.TextField(blank=True)

    # Timestamps. created_at is set on insert; started_at/finished_at
    # are nullable because a queued run hasn't started processing yet.
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        # Newest first — matches how dashboards and admins list runs.
        ordering = ["-created_at"]

        # Composite indexes for the two most common query patterns:
        #   - lookup by file hash (idempotency check)
        #   - filter by bank_code + period (reporting)
        indexes = [
            models.Index(fields=["file_hash"]),
            models.Index(fields=["bank_code", "period"]),
        ]

    def __str__(self):
        return f"Run {self.id} [{self.status}] {self.bank_code}/{self.period}"


class ValidRecord(models.Model):
    """A row that passed every rule.

    Only written for the sync path. The Spark path writes valid rows
    to a Delta table on MinIO instead, so this table stays small in
    production.
    """

    # Same UUID rationale as ValidationRun: distributed-safe, no
    # sequential leaks, and consistent across stores.
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Cascade delete: deleting a run removes its records. Correct
    # semantics — records have no meaning without their run.
    # related_name="valid_records" gives ``run.valid_records.all()``.
    run = models.ForeignKey(
        ValidationRun,
        on_delete=models.CASCADE,
        related_name="valid_records",
    )

    # 1-indexed row number from the source file. Used to correlate
    # back to a specific line in the CSV/JSON.
    row_number = models.IntegerField()

    # Fields from the input, kept as-is for downstream consumers.
    # Indexed because they're common filter/join columns.
    bank_code = models.CharField(max_length=32, db_index=True)
    period = models.CharField(max_length=16, db_index=True)
    account_code = models.CharField(max_length=64, db_index=True)

    # Money as Decimal. Never FloatField. max_digits=18 gives 16
    # integer digits + 2 decimals — enough for any bank amount.
    debit = models.DecimalField(max_digits=18, decimal_places=2)
    credit = models.DecimalField(max_digits=18, decimal_places=2)
    balance = models.DecimalField(max_digits=18, decimal_places=2)

    # Optional fields from the spec. blank=True allows empty strings.
    record_id = models.CharField(max_length=64, blank=True, db_index=True)
    currency = models.CharField(max_length=8, blank=True)
    branch_code = models.CharField(max_length=32, blank=True)
    description = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # Stable, source-order listing per run. Matches the ordering
        # of the ``/valid`` endpoint.
        ordering = ["run", "row_number"]

        # One composite index for the API listing (run + row order)
        # and one for cross-run joins by natural key.
        indexes = [
            models.Index(fields=["run", "row_number"]),
            models.Index(fields=["bank_code", "period", "account_code"]),
        ]

    def __str__(self):
        return f"Valid {self.run_id}#{self.row_number}"


class InvalidRecord(models.Model):
    """A row that failed at least one rule.

    Carries the error codes, human-readable messages, and the original
    input. Only written for the sync path; async runs use Delta on
    MinIO.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    run = models.ForeignKey(
        ValidationRun,
        on_delete=models.CASCADE,
        related_name="invalid_records",
    )

    row_number = models.IntegerField()

    # The original record, exactly as parsed from the file.
    # Why this matters:
    #   - Audit — the bank sees exactly what they sent
    #   - Replay — a fixed rule can re-validate without the source
    #   - Debug — a reviewer can inspect the failing values
    raw_row = models.JSONField()

    # Machine-readable failure codes: ``["E004", "E007"]``.
    # A list (not a single string) because one row can fail multiple
    # rules. The engine collects all failures per row.
    error_codes = models.JSONField(default=list)

    # Human-readable messages matching the codes 1:1 by position.
    # ``["Bank code is not in the allow-list: 999", "Balance ..."]``.
    error_messages = models.JSONField(default=list)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["run", "row_number"]

        # Single index — this table is queried by run and row_number,
        # which the composite index covers. No cross-run queries here.
        indexes = [
            models.Index(fields=["run", "row_number"]),
        ]

    def __str__(self):
        # Join the codes for readability in the admin and logs.
        # ``or []`` guards against a None value in edge cases.
        codes = ",".join(self.error_codes or [])
        return f"Invalid {self.run_id}#{self.row_number} [{codes}]"