"""Django management command to seed BankReference from data/reference/bank_codes.csv."""
from __future__ import annotations

import csv
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.validation.models import BankReference


class Command(BaseCommand):
    help = "Seed BankReference table from data/reference/bank_codes.csv"

    def add_arguments(self, parser):
        parser.add_argument(
            "--path",
            default=str(Path(settings.BASE_DIR) / "data" / "reference" / "bank_codes.csv"),
            help="Path to the bank_codes.csv file",
        )

    def handle(self, *args, **options):
        path = Path(options["path"])
        if not path.exists():
            self.stderr.write(self.style.ERROR(f"File not found: {path}"))
            return

        created = 0
        updated = 0

        with path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                code = (row.get("bank_code") or "").strip()
                if not code:
                    continue
                is_active = (row.get("is_active") or "true").strip().lower() in (
                    "true", "1", "yes", "y"
                )
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

        self.stdout.write(
            self.style.SUCCESS(f"BankReference seeded: {created} created, {updated} updated")
        )