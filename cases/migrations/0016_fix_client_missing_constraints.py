# cases_client is missing constraints models.py has always declared for it
# (the primary key on id, and the unique constraints on name/code) — real,
# on the production database, discovered while adding a new foreign key onto
# this table (marketing.ClientLabel.client) failed with "there is no unique
# constraint matching given keys for referenced table cases_client", which a
# table with a normal Django-created primary key cannot hit. Confirmed
# directly against the database before writing this: 582 rows, id already
# fully distinct, no duplicate name, no duplicate code — so restoring these
# three constraints is a pure integrity fix, not a data change, and cannot
# fail on the data that is actually there today.
#
# PostgreSQL only, and a no-op everywhere else: every SQLite database in this
# project (dev, tests) was always created fresh through a normal migration
# run, which already includes these constraints from CreateModel — this
# specific gap is unique to however the production Postgres table's history
# diverged from its own migrations, not something dev/test can ever exhibit.
#
# Idempotent (checks pg_constraint before adding each one) so it is safe to
# re-run against a database this specific gap never affected, or one that is
# only missing some of the three.
from django.db import migrations

_CHECKS = (
    ("cases_client_pkey", "p", "ALTER TABLE cases_client ADD PRIMARY KEY (id)"),
    ("cases_client_name_key", "u", "ALTER TABLE cases_client ADD CONSTRAINT cases_client_name_key UNIQUE (name)"),
    ("cases_client_code_key", "u", "ALTER TABLE cases_client ADD CONSTRAINT cases_client_code_key UNIQUE (code)"),
)


def fix_missing_constraints(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "SELECT conname, contype FROM pg_constraint WHERE conrelid = 'cases_client'::regclass"
        )
        existing = {(name, kind) for name, kind in cursor.fetchall()}
        has_pk = any(kind == "p" for _name, kind in existing)
        for name, kind, sql in _CHECKS:
            if kind == "p":
                if has_pk:
                    continue
            elif (name, kind) in existing:
                continue
            cursor.execute(sql)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('cases', '0015_case_marketing_label'),
    ]

    operations = [
        migrations.RunPython(fix_missing_constraints, reverse_code=noop_reverse),
    ]
