import uuid

from django.db import models


class BankReference(models.Model):
    """Allow-list of valid bank codes (from reference data)."""
    bank_code = models.CharField(max_length=32, unique=True, db_index=True)
    name = models.CharField(max_length=128, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["bank_code"]

    def __str__(self):
        return f"{self.bank_code} — {self.name}"


class ValidationRun(models.Model):
    """One upload/validation run. Anchor for audit + idempotency."""

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    bank_code = models.CharField(max_length=32, blank=True, db_index=True)
    period = models.CharField(max_length=16, blank=True, db_index=True)

    file_hash = models.CharField(max_length=64, db_index=True)
    source_file = models.CharField(max_length=512)
    file_size_bytes = models.BigIntegerField(default=0)

    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.QUEUED,
        db_index=True,
    )

    total_records = models.IntegerField(default=0)
    valid_count = models.IntegerField(default=0)
    invalid_count = models.IntegerField(default=0)
    duplicate_count = models.IntegerField(default=0)
    errors_by_code = models.JSONField(default=dict, blank=True)

    error_message = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["file_hash"]),
            models.Index(fields=["bank_code", "period"]),
        ]

    def __str__(self):
        return f"Run {self.id} [{self.status}] {self.bank_code}/{self.period}"


class ValidRecord(models.Model):
    """A row that passed all validation rules. Mirrors the curated (silver) zone."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(
        ValidationRun,
        on_delete=models.CASCADE,
        related_name="valid_records",
    )

    row_number = models.IntegerField()

    bank_code = models.CharField(max_length=32, db_index=True)
    period = models.CharField(max_length=16, db_index=True)
    account_code = models.CharField(max_length=64, db_index=True)

    debit = models.DecimalField(max_digits=18, decimal_places=2)
    credit = models.DecimalField(max_digits=18, decimal_places=2)
    balance = models.DecimalField(max_digits=18, decimal_places=2)

    record_id = models.CharField(max_length=64, blank=True, db_index=True)
    currency = models.CharField(max_length=8, blank=True)
    branch_code = models.CharField(max_length=32, blank=True)
    description = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["run", "row_number"]
        indexes = [
            models.Index(fields=["run", "row_number"]),
            models.Index(fields=["bank_code", "period", "account_code"]),
        ]

    def __str__(self):
        return f"Valid {self.run_id}#{self.row_number}"


class InvalidRecord(models.Model):
    """A row that failed at least one rule. Mirrors the quarantine zone."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(
        ValidationRun,
        on_delete=models.CASCADE,
        related_name="invalid_records",
    )

    row_number = models.IntegerField()

    raw_row = models.JSONField()
    error_codes = models.JSONField(default=list)
    error_messages = models.JSONField(default=list)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["run", "row_number"]
        indexes = [
            models.Index(fields=["run", "row_number"]),
        ]

    def __str__(self):
        codes = ",".join(self.error_codes or [])
        return f"Invalid {self.run_id}#{self.row_number} [{codes}]"