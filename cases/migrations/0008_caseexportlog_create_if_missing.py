"""Make the export-audit table creatable by migration, not only by hand.

The second half of item 7. Migration 0003 adopted ``CaseExportLog`` into
migration *state* with an empty ``database_operations`` list, because the table
already existed on the live database at the time. That was right for the live
database and wrong for every other one: since the ``cases`` app has migrations,
``migrate --run-syncdb`` will not create the table either, so the only thing in
the entire codebase that has ever created ``cases_caseexportlog`` is the
hand-written DDL in the start-up patcher.

That matters now, because the plan is to retire that patcher. Turning it off
while this table still depended on it would leave every fresh install without
it, and the first attempt to export a document would fail with "no such table"
— on a brand-new deployment, where it is least expected. The state is already
correct from 0003, so all that is needed is the database half.

Mirrors 0007: create the table only where it is missing, so an established
database is untouched and a fresh one gets the real thing — proper foreign keys
and the declared index rather than the hand-written per-vendor SQL.
"""
from django.db import migrations

_TABLE = "cases_caseexportlog"


def _create_if_missing(apps, schema_editor):
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        existing = set(connection.introspection.table_names(cursor))
    if _TABLE in existing:
        return
    schema_editor.create_model(apps.get_model("cases", "CaseExportLog"))


def _drop_if_present(apps, schema_editor):
    """Reverse: drop the table if present.

    Deliberately not a no-op — after this migration the table belongs to the
    migration history — but it does delete the export audit rows, so reversing
    is not something to do casually on production.
    """
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        existing = set(connection.introspection.table_names(cursor))
    if _TABLE not in existing:
        return
    schema_editor.delete_model(apps.get_model("cases", "CaseExportLog"))


class Migration(migrations.Migration):

    dependencies = [
        ("cases", "0007_casecurrencylog_into_migrations"),
    ]

    # The model is already in migration state from 0003, so this is the
    # database half on its own — no SeparateDatabaseAndState needed, and
    # apps.get_model resolves correctly.
    operations = [
        migrations.RunPython(_create_if_missing, _drop_if_present),
    ]
