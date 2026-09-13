from rest_framework import serializers

from apps.validation.models import InvalidRecord, ValidRecord, ValidationRun


class ValidationRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = ValidationRun
        fields = [
            "id", "bank_code", "period", "source_file", "file_hash",
            "status", "total_records", "valid_count", "invalid_count",
            "duplicate_count", "errors_by_code", "error_message",
            "created_at", "started_at", "finished_at",
        ]


class InvalidRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvalidRecord
        fields = ["id", "row_number", "error_codes", "error_messages", "raw_row"]


class ValidRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = ValidRecord
        fields = [
            "id", "row_number", "bank_code", "period", "account_code",
            "debit", "credit", "balance", "record_id", "currency",
            "branch_code", "description",
        ]