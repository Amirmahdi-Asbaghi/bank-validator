"""DRF serializers for the read endpoints.

Each serializer maps a Django model instance to a JSON-friendly dict
for the API response. The field lists here are the API's public
contract — every field named here is promised to clients; every field
omitted is intentionally private or internal.

Why ModelSerializer and not plain Serializer:
    These serializers expose model fields with no custom logic. DRF's
    ModelSerializer generates field definitions from the model's field
    types automatically. Only the field *list* needs to be declared.

Why not expose every field:
    The models have fields that shouldn't be public or aren't useful
    to clients — primary-key internals, ownership FKs, timestamps that
    duplicate other data. The lists here are curated, not exhaustive.
"""
from rest_framework import serializers

from apps.validation.models import InvalidRecord, ValidRecord, ValidationRun


class ValidationRunSerializer(serializers.ModelSerializer):
    """Serialize a ValidationRun — the summary of one upload.

    Used by:
        - POST /api/v1/validation     (the response after upload)
        - GET  /api/v1/validation/{id}

    The response includes the metadata (id, source_file, file_hash,
    timestamps), the run state (status, error_message), and the
    aggregate counts (total_records, valid_count, ..., errors_by_code).

    What's NOT included:
        - ``created_at``, ``started_at``, ``finished_at`` — actually
          these ARE included below. The list intentionally exposes all
          three timestamps so clients can compute queue wait and
          processing time from a single response.
    """

    class Meta:
        model = ValidationRun

        # Curated field list. Order doesn't matter for JSON output but
        # affects the OpenAPI schema and the Swagger UI rendering.
        # Grouped by purpose: identity, source, status, counts, time.
        fields = [
            # Identity
            "id",                # UUID — used to poll the run
            "bank_code",         # metadata (currently empty; see notes)
            "period",            # metadata (currently empty)

            # Source file
            "source_file",       # s3://raw/uploads/<uuid>.csv
            "file_hash",         # SHA-256 of the file content

            # Run state
            "status",            # queued / running / completed / failed
            "error_message",     # populated only when status=failed

            # Aggregate counts
            "total_records",
            "valid_count",
            "invalid_count",
            "duplicate_count",
            "errors_by_code",    # {"E004": 9034, "E007": 9800, ...}

            # Timestamps — three distinct moments:
            #   created_at   when the row was inserted (upload received)
            #   started_at   when processing began
            #   finished_at  when processing completed
            "created_at",
            "started_at",
            "finished_at",
        ]


class InvalidRecordSerializer(serializers.ModelSerializer):
    """Serialize an InvalidRecord — one failed row.

    Used by GET /api/v1/validation/{id}/invalid.

    The three fields unique to this serializer:
        error_codes     machine-readable list (["E004", "E007"])
        error_messages  human-readable list, aligned 1:1 with codes
        raw_row         the original input, never modified

    Why no typed data fields (debit, credit, balance)?
        Because the values are unreliable — that's why the row failed.
        A debit of "abc" can't be serialized as a Decimal. Keeping
        only raw_row avoids this problem and preserves the exact input.
    """

    class Meta:
        model = InvalidRecord

        fields = [
            "id",               # UUID for pagination/linking
            "row_number",       # 1-indexed line in the source file
            "error_codes",      # JSONField → list of strings
            "error_messages",   # JSONField → list of strings
            "raw_row",          # JSONField → dict of the original row
        ]


class ValidRecordSerializer(serializers.ModelSerializer):
    """Serialize a ValidRecord — one passing row.

    Used by GET /api/v1/validation/{id}/valid.

    All data fields are exposed as-is. Decimal fields (debit, credit,
    balance) are serialized as strings by DRF to preserve precision —
    a float would lose the exact value.

    Optional fields (record_id, currency, branch_code, description)
    are included even when empty. Clients see consistent keys
    regardless of what the bank sent.
    """

    class Meta:
        model = ValidRecord

        fields = [
            # Position
            "id",
            "row_number",

            # Natural key
            "bank_code",
            "period",
            "account_code",

            # Money — serialized as strings to preserve Decimal precision
            "debit",
            "credit",
            "balance",

            # Optional fields from the spec
            "record_id",
            "currency",
            "branch_code",
            "description",
        ]