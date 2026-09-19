from rest_framework import serializers

from bank_validator.models import BankRecord


class BankRecordSerializer(serializers.ModelSerializer):
    # Serializes one validated record, including its verdict and errors.
    # Used by the endpoint that returns all records for a given run_id.
    class Meta:
        model = BankRecord
        fields = (
            "run_id",
            "bank_code",
            "period",
            "account_code",
            "debit",
            "credit",
            "balance",
            "valid",
            "errors",
            "timestamp",
        )


class SummarySerializer(serializers.Serializer):
    # Describes the nested summary object returned after a validation run.
    # Holds the totals plus a per-error-code count.
    total = serializers.IntegerField()
    valid = serializers.IntegerField()
    invalid = serializers.IntegerField()
    errors_by_code = serializers.DictField(child=serializers.IntegerField())


class ValidationResponseSerializer(serializers.Serializer):
    # Shapes the response of the upload/validate endpoint.
    # It wraps the run_id together with the nested summary.
    run_id = serializers.CharField()
    summary = SummarySerializer()