# One-way data cleanup — see the module-level comment below for why.

from django.db import migrations


def clear_manual_marketing_data_again(apps, schema_editor):
    """Wipe every existing ClientLabel and Connection row, a second time.

    ``0006_clear_manual_marketing_data`` did this once already, for the same
    reason: the Attach/connection logic behind these two tables keeps being
    redesigned — the case's own ``marketing_label`` field becoming the sole,
    authoritative source of a case-derived role (see ``cases.models.Case``
    and ``marketing/services.py::_effective_label``), a chart connection now
    also registering the target's role automatically
    (``marketing/services.py::create_connection``), the active-anchor picker
    behaviour changing again — enough that, once again, the owner asked for a
    clean slate to re-test connecting things under the CURRENT rules rather
    than trying to reason about rows a prior version of the logic left
    behind. Same two tables, same scope, same reasoning as last time:

    * ``ClientLabel`` — the hand-curated tags a Marketing user attaches to a
      company under a field (sponsor, owner, licensor, ...).
    * ``Connection`` — the company-to-company links drawn between two
      fields (e.g. an EPC contractor's own sub-supplier).

    ALSO CLEARS THE TIMELINE ROWS THOSE TWO TABLES' OWN WRITES LOGGED —
    ``ClientEvent`` rows with action ``LABEL_ADDED``, ``LABEL_REMOVED``,
    ``CONNECTION_ADDED`` or ``CONNECTION_REMOVED`` — which ``0006`` did not
    do (it predates most of that logging). Left in place, they would be
    history entries on a company's own timeline narrating connections that
    no longer exist after this migration deletes the rows they describe;
    keeping them would be strictly more misleading than the stale
    ``ClientLabel``/``Connection`` rows this migration already existed to
    remove. Every OTHER ``ClientEvent`` action is untouched —
    ``CLIENT_REGISTERED``, ``CONTACT_ADDED``/``CONTACT_REMOVED``,
    ``REPORT_ADDED`` and ``CASE_ROLE_CHANGED`` are real history unrelated to
    the manual label/connection graph this migration resets, and
    ``CASE_ROLE_CHANGED`` in particular is case-derived, not manual — see
    below.

    Nothing else is touched. This does NOT reach into ``cases.models.Case``
    or any other case-derived fact — those are read live, not stored in
    either of these tables, and are untouched by this migration. Reverse is
    deliberately ``RunPython.noop``: this is a one-way cleanup, not meant to
    restore the very rows it exists to clear.
    """
    ClientLabel = apps.get_model('marketing', 'ClientLabel')
    Connection = apps.get_model('marketing', 'Connection')
    ClientEvent = apps.get_model('marketing', 'ClientEvent')
    ClientLabel.objects.all().delete()
    Connection.objects.all().delete()
    ClientEvent.objects.filter(action__in=[
        'LABEL_ADDED', 'LABEL_REMOVED', 'CONNECTION_ADDED', 'CONNECTION_REMOVED',
    ]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('marketing', '0010_alter_clientevent_action'),
    ]

    operations = [
        migrations.RunPython(clear_manual_marketing_data_again, migrations.RunPython.noop),
    ]
