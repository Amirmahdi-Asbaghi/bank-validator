from django.contrib import admin
from .models import BankRecord, ValidationRun


@admin.register(BankRecord)
class BankRecordAdmin(admin.ModelAdmin):
    # the columns that gone be shown in web ui
    list_display = ("run_id", "timestamp",
                    "bank_code", "period", "account_code", "debit", "credit", "balance",
                    "valid", "errors")

    search_fields = ("run_id", "timestamp",
                    "bank_code", "period", "account_code", "debit", "credit", "balance",
                    "valid", "errors")
    readonly_fields = ("run_id", "timestamp")


@admin.register(ValidationRun)
class ValidationRunAdmin(admin.ModelAdmin):
    list_display = ("run_id", "timestamp", "file_name", "total", "valid_count", "invalid_count", "errors_by_code")
    search_fields = ("run_id", "file_name")
    readonly_fields = ("run_id", "timestamp", "file_name", "total", "valid_count", "invalid_count", "errors_by_code")



