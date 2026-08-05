"""Import a code table for one group from a CSV or Excel file into SQLite.

Use this on the real server for large tables (fitting can be many gigabytes).
Rows are streamed; the result is the same per-group SQLite DB that Tool Data
uses (not pandas / not Django row tables).

    python manage.py import_codes pipe /data/pipe_coding_data.csv
    python manage.py import_codes fitting /data/fitting_coding_data.csv
"""
from django.core.management.base import BaseCommand, CommandError

from itemcoder.importer import import_code_table


class Command(BaseCommand):
    help = "Import a group's code table (CSV/Excel) into the itemcoder SQLite DB."

    def add_arguments(self, parser):
        parser.add_argument("group", help="Group name, e.g. pipe or fitting.")
        parser.add_argument("path", help="Path to the CSV/Excel file on the server.")
        parser.add_argument(
            "--append",
            action="store_true",
            help="(Unsupported) Kept for compatibility; SQLite imports always replace.",
        )

    def handle(self, *args, **options):
        group = options["group"]
        path = options["path"]
        replace = not options["append"]

        self.stdout.write(self.style.NOTICE(
            f"Importing '{group}' from {path} (replace={replace}) ..."
        ))

        def progress(n):
            self.stdout.write(f"  ... {n:,} rows", ending="\r")

        try:
            summary = import_code_table(
                group, path, filename=path, replace=replace, progress=progress
            )
        except FileNotFoundError:
            raise CommandError(f"File not found: {path}")
        except Exception as exc:
            raise CommandError(str(exc))

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"Done: {summary['rows']:,} rows, {summary['columns']} columns "
            f"for group '{summary['group']}' → {summary.get('path', '')}"
        ))
