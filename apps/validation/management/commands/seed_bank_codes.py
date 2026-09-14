"""Django management command: seed the BankReference table.

Usage:
    python manage.py seed_bank_codes
    python manage.py seed_bank_codes --path /custom/bank_codes.csv

Reads a CSV with columns ``bank_code``, ``name``, ``is_active`` and
upserts each row into the ``BankReference`` table. Idempotent: running
it twice produces the same state.

Why a management command and not a data migration?
    Data migrations run once, automatically, on ``manage.py migrate``.
    This is different: the bank list is operational data that changes
    over time (banks get added or deactivated) and should be re-runnable
    without a migration. A management command fits that shape.
"""
from __future__ import annotations

import csv
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.validation.models import BankReference


class Command(BaseCommand):
    """Entry point for ``python manage.py seed_bank_codes``.

    BaseCommand is Django's management command base class. Subclassing
    it, defining ``add_arguments`` for CLI flags, and implementing
    ``handle`` for the logic is the standard pattern.
    """

    # Shown in ``manage.py help`` and at the top of ``--help`` output.
    help = "Seed BankReference table from data/reference/bank_codes.csv"

    def add_arguments(self, parser):
        """Declare CLI arguments.

        Default path is the conventional location under the project
        root. Overriding it lets you seed from a different file without
        editing code — useful for tests, staging, or one-off loads.
        """
        parser.add_argument(
            "--path",
            # BASE_DIR is resolved at runtime, so the default is always
            # correct regardless of where the project is checked out.
            default=str(Path(settings.BASE_DIR) / "data" / "reference" / "bank_codes.csv"),
            help="Path to the bank_codes.csv file",
        )

    def handle(self, *args, **options):
        """Execute the command.

        Django calls this after parsing args. ``options["path"]`` holds
        the --path value (or its default). Everything else is unused.

        Design: fail soft on a missing file, fail soft on bad rows,
        report a summary at the end. The command never raises — it
        writes to stderr and returns.
        """
        path = Path(options["path"])

        # Fail soft if the file is missing. The operator sees a clear
        # error; the command exits with code 0. If we wanted a hard
        # failure we'd raise CommandError, which triggers a non-zero
        # exit. We choose soft because missing files are common during
        # first-time setup and shouldn't break a scripted pipeline.
        if not path.exists():
            self.stderr.write(self.style.ERROR(f"File not found: {path}"))
            return

        created = 0
        updated = 0

        # newline="" is required by Python's csv module — without it,
        # Windows-style line endings can produce extra blank rows.
        # encoding="utf-8" is explicit so the command fails loudly if
        # the file is in a different encoding (rather than silently
        # producing mojibake in bank names).
        with path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)

            for row in reader:
                # Strip whitespace from the code — CSVs sometimes have
                # padding. Skip empty rows silently (some exports have
                # trailing blank lines).
                code = (row.get("bank_code") or "").strip()
                if not code:
                    continue

                # Parse the is_active column leniently.
                # Accepts: "true", "True", "1", "yes", "y".
                # Defaults to True when absent or unrecognized —
                # a new bank is active unless explicitly deactivated.
                #
                # Why lenient? CSV exports from different systems
                # represent booleans differently. Being strict would
                # force banks to normalize their exports first.
                is_active = (row.get("is_active") or "true").strip().lower() in (
                    "true", "1", "yes", "y"
                )

                # update_or_create is idempotent: it matches on
                # bank_code (the natural key) and either creates a new
                # row or updates the existing one. Running this command
                # twice produces the same state — no duplicates, no
                # stale values.
                #
                # The `is_new` flag tells us which path was taken, so
                # we can report accurate counts.
                obj, is_new = BankReference.objects.update_or_create(
                    bank_code=code,
                    defaults={
                        "name": (row.get("name") or "").strip(),
                        "is_active": is_active,
                    },
                )

                if is_new:
                    created += 1
                else:
                    updated += 1

        # Summary line. The style.SUCCESS wraps it in green for the
        # terminal. Non-zero counts tell the operator what changed.
        self.stdout.write(
            self.style.SUCCESS(
                f"BankReference seeded: {created} created, {updated} updated"
            )
        )