from django.shortcuts import render

# Create your views here.
from django.shortcuts import get_object_or_404
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.validation.models import InvalidRecord, ValidRecord, ValidationRun

from .serializers import (
    InvalidRecordSerializer,
    ValidRecordSerializer,
    ValidationRunSerializer,
)


class InvalidPagination(PageNumberPagination):
    page_size = 100
    page_size_query_param = "page_size"
    max_page_size = 1000


class RunDetailView(APIView):
    def get(self, request, run_id):
        run = get_object_or_404(ValidationRun, id=run_id)
        return Response(ValidationRunSerializer(run).data)


class RunSummaryView(APIView):
    def get(self, request, run_id):
        run = get_object_or_404(ValidationRun, id=run_id)
        return Response(
            {
                "run_id": str(run.id),
                "bank_code": run.bank_code,
                "period": run.period,
                "status": run.status,
                "total": run.total_records,
                "valid": run.valid_count,
                "invalid": run.invalid_count,
                "duplicates": run.duplicate_count,
                "errors_by_code": run.errors_by_code,
                "created_at": run.created_at,
                "finished_at": run.finished_at,
            }
        )


class RunInvalidView(APIView):
    def get(self, request, run_id):
        run = get_object_or_404(ValidationRun, id=run_id)
        qs = InvalidRecord.objects.filter(run=run).order_by("row_number")
        paginator = InvalidPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = InvalidRecordSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class RunValidView(APIView):
    def get(self, request, run_id):
        run = get_object_or_404(ValidationRun, id=run_id)
        qs = ValidRecord.objects.filter(run=run).order_by("row_number")
        paginator = InvalidPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = ValidRecordSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)