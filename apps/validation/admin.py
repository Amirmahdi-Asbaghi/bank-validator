"""Django admin registrations for the validation models.

The admin is a free ops console. It's not a replacement for a proper
dashboard, but for a portfolio project it demonstrates three things:

    1. The models are designed to be inspectable
    2. Common query patterns are pre-configured (filters, search)
    3. Dangerous fields (id, timestamps) are read-only

Design notes:

    - ``list_display`` shows the columns an operator actually looks at,
      not every field on the model.
    - ``list_filter`` turns frequent filter operations into one click.
    - ``search_fields`` enables fast lookup by the identifiers people
      actually type (run id, file hash, account code).
    - ``readonly_fields`` prevents accidental edits to identity and
      audit fields.
"""
from django.contrib import admin

from .models import BankReference, InvalidRecord, ValidRecord, ValidationRun


@admin.register(BankReference)
class BankReferenceAdmin(admin.ModelAdmin):
    """Bank allow-list. Edited when a new bank is supervised."""

    # The columns an ops person scans: code, name, active flag,
    # and when the row was added. Everything else is noise here.
    list_display = ("bank_code", "name", "is_active", "created_at")

    # Filter by active status — the most common operation is "show
    # only active banks" or "show deactivated ones".
    list_filter = ("is_active",)

    # Search by code or name. Both are natural ways to find a bank.
    search_fields = ("bank_code", "name")


@admin.register(ValidationRun)
class ValidationRunAdmin(admin.ModelAdmin):
    """Runs list — the ops console's main screen.

    An operator opens this page to answer: "which runs are stuck?",
    "what was the last run for bank 010?", "did that file fail?".
    The list_display and list_filter are tuned for those questions.
    """

    # The order here is intentional: identity first, then status,
    # then counts, then timestamp. Matches how an operator reads a run.
    list_display = (
        "id",
        "bank_code",
        "period",
        "status",
        "total_records",
        "valid_count",
        "invalid_count",
        "duplicate_count",
        "created_at",
    )

    # Filter by status (find queued or failed runs), bank, period.
    # These are the three dimensions an operator filters by.
    list_filter = ("status", "bank_code", "period")

    # Search by id, file_hash, or the source file path. These are the
    # three identifiers someone might have in hand when looking up a run.
    search_fields = ("id", "file_hash", "source_file")

    # Identity and audit fields are read-only. Nothing should mutate
    # them through the admin — the run id and timestamps are facts,
    # not editable state.
    #
    # Note: this does NOT prevent programmatic updates. The engine
    # still sets started_at/finished_at during processing. read-only
    # here only means "not editable through the admin forms".
    readonly_fields = ("id", "created_at", "started_at", "finished_at")


@admin.register(ValidRecord)
class ValidRecordAdmin(admin.ModelAdmin):
    """Individual valid records.

    This table can grow large. Admins don't usually browse it globally —
    they drill in from a specific run. But when they do land here,
    the filters help narrow down quickly.
    """

    # Show the fields that identify a record at a glance: row number,
    # natural key, and the accounting equation components.
    list_display = (
        "row_number",
        "bank_code",
        "period",
        "account_code",
        "debit",
        "credit",
        "balance",
    )

    # Filter by natural key. Useful when investigating a single account
    # or a specific bank/period combination.
    list_filter = ("bank_code", "period")

    # Search by account code (the natural identifier) or record_id
    # (the bank's own ID when present).
    search_fields = ("account_code", "record_id")


@admin.register(InvalidRecord)
class InvalidRecordAdmin(admin.ModelAdmin):
    """Individual invalid records with their reasons.

    The list view is intentionally minimal — error_codes is a list, and
    the raw_row is a dict, so the full detail lives on the detail page.
    An operator clicks into a record to see the error messages and
    original data.
    """

    # Only the essentials in the list: which row, which run, what
    # failed, and when. Everything else is in the detail view.
    list_display = ("row_number", "run", "error_codes", "created_at")

    # Filter by run — the most common way to view invalid records
    # ("show me all failures for this run").
    list_filter = ("run",)

    # Search by row_number so an operator can jump to a specific line
    # from a support ticket.
    search_fields = ("row_number",)