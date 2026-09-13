from django.urls import path

from .views import ValidationUploadView

urlpatterns = [
    path("validation", ValidationUploadView.as_view(), name="validation-upload"),
]