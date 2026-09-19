from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from bank_validator.models import BankRecord
from bank_validator.serializers import BankRecordSerializer, ValidationResponseSerializer
from bank_validator.services import process_csv, process_json


class ValidationView(APIView):

    def post(self, request):
        file = request.FILES.get("file")
        try:
            if file is not None:
                name = file.name.lower()
                if name.endswith(".csv"):
                    result = process_csv(file, file_name=file.name)
                elif name.endswith(".json"):
                    result = process_json(file, file_name=file.name)
                else:
                    return Response(
                        {"error": "unsupported file type"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
            elif request.content_type == "application/json":
                result = process_json(request.body)
            else:
                return Response(
                    {"error": "no file or JSON body provided"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        serializer = ValidationResponseSerializer(result)
        return Response(serializer.data, status=status.HTTP_200_OK)


class RunRecordsView(APIView):

    def get(self, request, run_id):
        records = BankRecord.objects.filter(run_id=run_id)

        if not records.exists():
            return Response(
                {"error": "run not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = BankRecordSerializer(records, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)