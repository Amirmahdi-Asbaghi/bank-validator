from django.urls import path

from .views import RunDetailView, RunInvalidView, RunSummaryView, RunValidView

urlpatterns = [
    path("validation/<uuid:run_id>", RunDetailView.as_view(), name="run-detail"),
    path("validation/<uuid:run_id>/summary", RunSummaryView.as_view(), name="run-summary"),
    path("validation/<uuid:run_id>/invalid", RunInvalidView.as_view(), name="run-invalid"),
    path("validation/<uuid:run_id>/valid", RunValidView.as_view(), name="run-valid"),
]