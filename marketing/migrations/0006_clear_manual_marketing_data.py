# One-way data cleanup — see the module-level comment below for why.

from django.db import migrations


def clear_manual_marketing_data(apps, schema_editor):
    """Wipe every existing ClientLabel and Connection row.

    The owner asked for this, in their own words: the Attach/connection
    logic behind these two tables has been redesigned enough times — which
    field pairs with which, what counts as an edge, how a label gets
    attached — that the rows sitting in the database today were built under
    one or another earlier, since-changed version of that logic. They no
    longer mean what a row created under the CURRENT logic would mean, so
    keeping them around would just be misleading, not useful history. This
    is a deliberate clean slate for both tables, nothing more:

    * ``ClientLabel`` — the hand-curated tags a Marketing user attaches to a
      company under a field (sponsor, owner, licensor, ...).
    * ``Connection`` — the company-to-company links drawn between two
      fields (e.g. an EPC contractor's own sub-supplier).

    Nothing else is touched. This does NOT reach into ``cases.models.Case``
    or any other case-derived fact — those are read live, not stored in
    either of these tables, and are untouched by this migration. Reverse is
    deliberately ``RunPython.noop``: this is a one-way cleanup, not meant to
    restore the very rows it exists to clear.
    """
    ClientLabel = apps.get_model('marketing', 'ClientLabel')
    Connection = apps.get_model('marketing', 'Connection')
    ClientLabel.objects.all().delete()
    Connection.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('marketing', '0005_connection'),
    ]

    operations = [
        migrations.RunPython(clear_manual_marketing_data, migrations.RunPython.noop),
    ]
