"""URL routing for the bank validation project.

Every incoming request is matched against ``urlpatterns`` in order.
The first pattern that matches determines the view. If no pattern
matches, Django returns 404.

Structure:

    /admin/                 Django admin
    /api/v1/validation      POST — upload a file
    /api/v1/validation/{id}         — run detail
    /api/v1/validation/{id}/summary — run summary
    /api/v1/validation/{id}/valid   — paginated valid records
    /api/v1/validation/{id}/invalid — paginated invalid records
    /api/schema/            OpenAPI JSON (machine-readable)
    /api/schema/swagger/    Swagger UI (human-readable)
    /metrics                Prometheus scrape endpoint

Why a version prefix (``/api/v1/``):
    So the API can evolve without breaking existing clients. When we
    need to change a response shape incompatibly, we add ``/api/v2/``
    and keep ``/api/v1/`` working. This is a standard REST practice
    and one of the cheaper ways to buy future flexibility.

Why ``include()``:
    Each app owns its own URL module (``apps/ingestion/urls.py`` and
    ``apps/reports/urls.py``). This keeps route definitions close to
    the views they serve. Adding a new endpoint means editing the
    app's urls.py, not this file.
"""
from django.contrib import admin
from django.urls import include, path
from apps.common.metrics import metrics_view
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
)

urlpatterns = [
    # -------- Django admin --------
    # The free ops console — browse models, edit the bank allow-list,
    # inspect runs. In production, this would be behind SSO or
    # restricted by IP. For a portfolio project, it's open.
    path("admin/", admin.site.urls),

    # -------- API v1 --------
    # Two includes, both under the same ``/api/v1/`` prefix. Django
    # tries each include in order; if the first doesn't match the
    # remaining path, it moves to the second.
    #
    # Why split:
    #   - ingestion owns the write path (upload)
    #   - reports owns the read path (detail, summary, listings)
    #
    # The split mirrors the app structure, not the HTTP verbs.
    # A future refactor might merge them or split further
    # (e.g. ``ingestion`` and ``queries``); the current grouping is
    # by domain, not by direction.
    path("api/v1/", include("apps.ingestion.urls")),
    path("api/v1/", include("apps.reports.urls")),

    # -------- OpenAPI schema + Swagger UI --------
    #
    # ``/api/schema/`` serves the raw OpenAPI JSON document. This is
    # the machine-readable contract — clients can import it into
    # Postman, Insomnia, or a code generator.
    #
    # Why the trailing slash matters:
    #   Django's ``APPEND_SLASH`` setting (default True) redirects
    #   ``/api/schema`` to ``/api/schema/`` with a 301. The trailing
    #   slash is declared explicitly here to avoid the redirect.
    path(
        "api/schema/",
        SpectacularAPIView.as_view(),
        name="schema",
    ),

    # ``/api/schema/swagger/`` renders the OpenAPI spec as an
    # interactive HTML page. The ``url_name="schema"`` argument tells
    # Swagger UI which URL to fetch the spec from — Django reverses
    # it via ``reverse("schema")``, so the spec URL stays in sync
    # even if we move it.
    #
    # Why the URL is under /api/schema/ and not /api/docs/:
    #   Both conventions exist. The `/api/schema/` prefix groups the
    #   two related endpoints (JSON + HTML) under one parent.
    path(
        "api/schema/swagger/",
        SpectacularSwaggerView.as_view(url_name="schema"),
    ),

    # -------- Prometheus metrics --------
    #
    # No trailing slash — this is a convention with Prometheus, which
    # scrapes the exact URL configured in its scrape config. Adding a
    # trailing slash would require APPEND_SLASH to redirect, which
    # Prometheus doesn't follow by default.
    #
    # The path is deliberately outside /api/ — it's not part of the
    # API contract. A future version might expose it on a separate
    # port for security (internal-only).
    path("metrics", metrics_view, name="metrics"),
]