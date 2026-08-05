"""Bring the currency-conversion audit table into the migration history.

Background — this is item 7 of the review document ("the database structure is
partly managed by hand"), fixed for the one table where the drift was real.

The ``cases`` app historically ran on ``migrate --run-syncdb`` plus a start-up
step that issued hand-written CREATE TABLE / ALTER TABLE statements for
anything the migration history did not cover, swallowing every error. That step
is why ``CaseCurrencyLog`` works today despite appearing in no migration at
all: the table was created by raw DDL on first connection, with different SQL
per database vendor, no foreign keys, and no record that it had happened. On a
database built purely by ``migrate`` the table would simply not exist, and
nothing would say so until somebody tried to convert a proforma's currency.

This migration closes that, and is written to be correct in both worlds:

* On an established database (production, and any machine that has been running
  the start-up patcher) the table already exists. It is adopted into migration
  state and no DDL is issued, so there is nothing to fail and no data is
  touched.
* On a fresh database Django creates it properly from the model definition —
  real foreign keys and the declared index, instead of the hand-written SQL.

The existence check runs at *execution* time, inside the migration operation,
deliberately. An earlier draft did the check in the class body; that runs while
the migration graph is still being imported, and opening a connection there
fires the ``connection_created`` signal that the start-up patcher listens on —
so the patcher created the table a moment before the check looked for it, and
the check then always reported "already exists". The migration would have been
permanently unable to do the thing it was written to do. Checking inside the
operation also means the correct database is used under ``--database=``, and
``makemigrations``/``showmigrations``/``sqlmigrate`` never touch the database
just to be loaded.

Companion change: the ``cases_casecurrencylog`` branch has been removed from
the start-up patcher, so this migration is now the only thing that creates the
table.
"""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

_TABLE = "cases_casecurrencylog"

_CREATE_MODEL = migrations.CreateModel(
    name="CaseCurrencyLog",
    fields=[
        ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                   serialize=False, verbose_name="ID")),
        ("from_code", models.CharField(blank=True, max_length=12)),
        ("to_code", models.CharField(blank=True, max_length=12)),
        ("rate", models.CharField(blank=True, max_length=40)),
        ("side", models.CharField(blank=True, max_length=10)),
        ("form_kind", models.CharField(blank=True, default="PI", max_length=10)),
        ("form_version", models.IntegerField(blank=True, null=True)),
        ("source", models.CharField(blank=True, max_length=20)),
        ("label", models.CharField(blank=True, max_length=200)),
        ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
        ("actor", models.ForeignKey(
            null=True, on_delete=django.db.models.deletion.SET_NULL,
            related_name="case_currency_conversions", to=settings.AUTH_USER_MODEL)),
        ("case", models.ForeignKey(
            on_delete=django.db.models.deletion.CASCADE,
            related_name="currency_logs", to="cases.case")),
    ],
    options={"ordering": ["-created_at"]},
)


def _create_if_missing(apps, schema_editor):
    """Create the table only where it does not already exist."""
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        existing = set(connection.introspection.table_names(cursor))
    if _TABLE in existing:
        return
    schema_editor.create_model(apps.get_model("cases", "CaseCurrencyLog"))


def _drop_if_present(apps, schema_editor):
    """Reverse: drop the table if it is there.

    Reversing this migration on an established database removes a table the
    start-up patcher originally created. That is the honest behaviour — after
    this migration the table is owned by the migration history — but it does
    delete the conversion audit rows, so reverse deliberately is not a no-op
    and should not be run casually on production.
    """
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        existing = set(connection.introspection.table_names(cursor))
    if _TABLE not in existing:
        return
    schema_editor.delete_model(apps.get_model("cases", "CaseCurrencyLog"))


class Migration(migrations.Migration):

    dependencies = [
        ("cases", "0006_signaturesnapshot_signer_title"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    # Two sibling operations, not one operation with a nested RunPython.
    #
    # SeparateDatabaseAndState deliberately ignores the state its own
    # state_operations produce when it runs its database_operations — it
    # rebuilds state from the operations in that inner list, and RunPython
    # contributes none. A RunPython nested inside would therefore be handed the
    # project state from *before* this migration, where CaseCurrencyLog does
    # not exist yet, and apps.get_model() would raise LookupError. It would
    # have passed silently on the production database (table already there, so
    # the lookup is never reached) and failed on every fresh install — the
    # exact asymmetry this migration exists to remove.
    #
    # Run as two operations, the first has already applied its state by the
    # time the second executes, so the historical model resolves correctly on
    # both kinds of database. This is also the shape already used by 0003.
    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[_CREATE_MODEL],
            database_operations=[],
        ),
        migrations.RunPython(_create_if_missing, _drop_if_present),
    ]
