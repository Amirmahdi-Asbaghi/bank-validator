from django.contrib import admin

from .models import BankReference, InvalidRecord, ValidRecord, ValidationRun


@admin.register(BankReference)
class BankReferenceAdmin(admin.ModelAdmin):
    list_display = ("bank_code", "name", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("bank_code", "name")


@admin.register(ValidationRun)
class ValidationRunAdmin(admin.ModelAdmin):
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
    list_filter = ("status", "bank_code", "period")
    search_fields = ("id", "file_hash", "source_file")
    readonly_fields = ("id", "created_at", "started_at", "finished_at")


@admin.register(ValidRecord)
class ValidRecordAdmin(admin.ModelAdmin):
    list_display = (
        "row_number",
        "bank_code",
        "period",
        "account_code",
        "debit",
        "credit",
        "balance",
    )
    list_filter = ("bank_code", "period")
    search_fields = ("account_code", "record_id")


@admin.register(InvalidRecord)
class InvalidRecordAdmin(admin.ModelAdmin):
    list_display = ("row_number", "run", "error_codes", "created_at")
    list_filter = ("run",)
    search_fields = ("row_number",)