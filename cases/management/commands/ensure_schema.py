"""Ensure post-initial-migration columns exist (idempotent).

Some columns (e.g. ``cases_caseform.two_stage``) were added to the models after
the initial migration was written. On an *existing* database the ``cases`` app
adds them automatically on the first connection. On a *brand-new* database the
tables are created by ``migrate`` only after that first connection has already
happened, so the auto-add can miss them.

Running ``python manage.py ensure_schema`` right after ``migrate`` closes that
gap: the tables already exist, so every required column is added. The operation
is safe to run any number of times and works on both SQLite and PostgreSQL.
"""
from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = (
        "Add any columns introduced after the initial migration that are "
        "missing from the database. Idempotent; safe on SQLite and PostgreSQL."
    )

    def handle(self, *args, **options):
        config = apps.get_app_config("cases")
        # Reuse the exact same idempotent logic the app runs on first connect.
        config._ensure_added_columns(connection)
        self.stdout.write(self.style.SUCCESS("Schema columns ensured."))
