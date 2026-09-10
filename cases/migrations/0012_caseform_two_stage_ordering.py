from django.db import migrations


class Migration(migrations.Migration):
    """Give ``CaseForm`` a total order so "the newest version" is a rule.

    A TO & PI two-stage snapshot deliberately keeps the version NUMBER of the
    version it supersedes ("01" and "01 · Two Stage" are both version 1), so
    ``ordering = ["kind", "-version"]`` left those two rows tied and whichever
    the database happened to return first won. ``-two_stage`` (the two-stage
    generation is the later one by definition) and then ``-id`` break the tie.

    Ordering is Python-side metadata — Django adds it to the ORDER BY it
    generates and never stores it — so this migration touches no table and no
    row. It exists so ``makemigrations --check`` stays clean.
    """

    dependencies = [
        ("cases", "0011_alter_caseevent_action"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="caseform",
            options={"ordering": ["kind", "-version", "-two_stage", "-id"]},
        ),
    ]
