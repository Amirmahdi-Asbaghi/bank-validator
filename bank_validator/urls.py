from django.urls import path

from bank_validator.views import RunRecordsView, ValidationView

urlpatterns = [
    path("validation/", ValidationView.as_view(), name="validation"),
    path("runs/<uuid:run_id>/", RunRecordsView.as_view(), name="run-records"),
]