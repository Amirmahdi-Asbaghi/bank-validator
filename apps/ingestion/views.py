from django.conf import settings
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.reports.serializers import ValidationRunSerializer

from .serializers import UploadSerializer
from .services import run_validation_async, run_validation_sync


class ValidationUploadView(APIView):
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, *args, **kwargs):
        serializer = UploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        upload = serializer.validated_data["file"]
        content = upload.read()
        threshold = getattr(settings, "SMALL_FILE_THRESHOLD", 50 * 1024 * 1024)

        if len(content) < threshold:
            run = run_validation_sync(upload.name, content)
            http_status = (
                status.HTTP_201_CREATED
                if run.status == "completed"
                else status.HTTP_400_BAD_REQUEST
            )
        else:
            run = run_validation_async(upload.name, content)
            http_status = status.HTTP_202_ACCEPTED

        return Response(ValidationRunSerializer(run).data, status=http_status)