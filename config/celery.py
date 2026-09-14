"""Celery application configuration.

Celery is a distributed task queue. In this project it's configured
but not currently used — the async path goes through Kafka and Spark.
Celery exists as the "small-file async" path that the architecture
diagrams mention, and as the standard async infrastructure for any
future background work.

Why this file exists:
    Django and Celery need to be introduced to each other. Celery is
    a general-purpose library — it doesn't know about Django's settings
    or models. This module:

      1. Sets DJANGO_SETTINGS_MODULE so Django can bootstrap.
      2. Creates the Celery app with our chosen name.
      3. Tells Celery to read its config from Django's settings,
         under the CELERY_ prefix.
      4. Enables autodiscovery of tasks from INSTALLED_APPS.

    Without this file, `celery -A config worker` would fail because
    Celery wouldn't know where its configuration lives.

Why the file is named ``celery.py`` and lives in ``config/``:
    ``celery -A config`` loads the module ``config`` — Django's
    project package — and looks for a Celery app instance inside it.
    ``config/__init__.py`` imports the app from this module, so
    Celery finds it.

    This is the standard Django+Celery pattern.
"""
import os
from celery import Celery

# -------- Step 1: Django settings --------

# Celery runs in a separate process from Django (the worker container),
# so it needs to know which settings module to load. Django uses
# DJANGO_SETTINGS_MODULE to find settings.py.
#
# ``setdefault`` (not ``os.environ[...] = ...``) means: only set it
# if not already set. This lets the environment override it, which
# matters for tests or alternate settings modules.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

# -------- Step 2: Create the Celery app --------

# The name "bankval" identifies this app in logs and in the Celery
# admin (if used). It also prefixes autodiscovered task names —
# tasks defined in apps.ingestion.tasks become ingestion.<name>.
app = Celery("bankval")

# -------- Step 3: Read config from Django settings --------

# ``config_from_object`` tells Celery to pull its configuration from
# Django's settings module. All settings prefixed with ``CELERY_``
# are mapped to Celery's config keys (with the prefix stripped).
#
# For example:
#     CELERY_BROKER_URL = "redis://redis:6379/0"
#     → Celery's broker_url = "redis://redis:6379/0"
#
#     CELERY_TASK_SERIALIZER = "json"
#     → Celery's task_serializer = "json"
#
# This means all Celery config lives in settings.py alongside the
# rest of the project config, not in a separate celeryconfig.py.
app.config_from_object("django.conf:settings", namespace="CELERY")

# -------- Step 4: Discover tasks --------

# ``autodiscover_tasks()`` scans every app in INSTALLED_APPS for a
# ``tasks.py`` module and imports it. Any @shared_task-decorated
# function in those modules becomes a Celery task.
#
# Without this, you'd have to list every task module manually, and
# adding a new app would require editing this file.
#
# The current state: no ``tasks.py`` exists in any app, so Celery
# starts with an empty task registry. The infrastructure is ready
# for future async work, but nothing uses it yet.
app.autodiscover_tasks()