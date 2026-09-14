"""DRF serializer for the upload endpoint.

The first line of defence for the API. Validates that:
    1. A file is present
    2. The extension is .csv or .json
    3. The size is under the 200 MB cap

If any check fails, DRF returns a 400 with a structured error body
mapping field → [messages]. No pipeline code runs.

Why these checks live here and not in the view:
    - Serializers are DRF's idiom for request validation
    - The checks run before the file is read into memory
    - The error response shape is standardized by DRF
    - The OpenAPI schema picks up the validation rules automatically
"""
from rest_framework import serializers


class UploadSerializer(serializers.Serializer):
    """Validate an upload request.

    Not a ModelSerializer — there's no model backing the request. The
    upload isn't persisted directly; it goes through the pipeline and
    produces a ValidationRun.
    """

    # FileField handles multipart parsing. DRF buffers small files in
    # memory and large files to disk automatically, so we don't have
    # to think about the size of what's coming in until we read it.
    file = serializers.FileField()

    def validate_file(self, f):
        """Extra validation for the file field.

        DRF calls ``validate_<fieldname>`` after the field's own
        validation passes. By the time we're here, ``f`` is guaranteed
        to be a valid uploaded file — but not necessarily the *right*
        kind of file.

        Two checks:

            1. Extension — the spec accepts CSV or JSON only.
            2. Size      — bounded to protect memory and downstream.

        Both failures raise ``ValidationError``. DRF collects them into
        a response like ``{"file": ["Only .csv and .json files ..."]}``.
        """
        # Normalize the extension check. ``.CSV`` and ``.Json`` should
        # be accepted — the case is a client-side convention, not a
        # semantic difference.
        name = f.name.lower()

        if not (name.endswith(".csv") or name.endswith(".json")):
            raise serializers.ValidationError(
                "Only .csv and .json files are accepted."
            )

        # 200 MB cap. Sized to bound:
        #   - Memory: the view reads the whole file into a variable
        #   - Downstream: Spark's first-batch latency on larger files
        #   - The sync path: files under 50 MB never hit this limit;
        #     the cap is a ceiling for the async path.
        #
        # 200 * 1024 * 1024 is exactly 209715200 bytes.
        if f.size > 200 * 1024 * 1024:
            raise serializers.ValidationError(
                "File exceeds the 200 MB limit."
            )

        # Return the file unchanged. Returning it lets DRF know the
        # validation passed and gives the view access via
        # ``serializer.validated_data["file"]``.
        return f