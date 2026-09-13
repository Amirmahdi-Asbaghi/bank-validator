"""Celery tasks for asynchronous validation of large files."""
from __future__ import annotations

from celery import shared_task
from django.utils import timezone

from apps.storage.locator import read_uploaded_file
from apps.validation.models import ValidationRun

from .services import run_validation_from_bytes


@shared_task(name="ingestion.validate_large_file", bind=True, max_retries=2)
def validate_large_file(self, run_id: str) -> str:
    """
    Runs the same rule engine, but off the request thread.
    Called with the UUID of a ValidationRun that was created in QUEUED state.
    """
    try:
        run = ValidationRun.objects.get(id=run_id)
    except ValidationRun.DoesNotExist:
        return f"run {run_id} not found"

    run.status = ValidationRun.Status.RUNNING
    run.started_at = timezone.now()
    run.save(update_fields=["status", "started_at"])

    try:
        content = read_uploaded_file(run.source_file)
        run_validation_from_bytes(run, content)
    except Exception as exc:  # noqa: BLE001
        run.status = ValidationRun.Status.FAILED
        run.error_message = f"{type(exc).__name__}: {exc}"
        run.finished_at = timezone.now()
        run.save()
        raise self.retry(exc=exc, countdown=10)

    return str(run.id)