from django.shortcuts import render

# Create your views here.
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.reports.serializers import ValidationRunSerializer

from .serializers import UploadSerializer
from .services import run_validation


class ValidationUploadView(APIView):
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, *args, **kwargs):
        serializer = UploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        upload = serializer.validated_data["file"]
        content = upload.read()

        run = run_validation(upload.name, content)

        http_status = (
            status.HTTP_201_CREATED
            if run.status == "completed"
            else status.HTTP_400_BAD_REQUEST
        )
        return Response(ValidationRunSerializer(run).data, status=http_status)