# Schema step 2 of 3 — the genuine DATA migration. See
# ``0012_contactphone.py``'s module comment for why this sits between the
# table being created and the old columns being dropped, and
# ``marketing/models.py::CompanyContact``/``ContactPhone`` for why the split
# exists at all.

from django.db import migrations


def move_single_phone_to_contact_phone(apps, schema_editor):
    """Copy every existing single phone number onto its own ``ContactPhone`` row.

    ONE ROW PER CONTACT THAT HAD ANYTHING TO COPY — not one row per contact
    unconditionally. A contact whose ``phone_prefix``/``phone``/``phone_ext``
    were all blank had no phone number on file before this change, and
    manufacturing an empty ``ContactPhone`` row for them would invent a phone
    entry nobody ever typed (and would fail this app's own "a row only counts
    as a real number once ``phone`` is non-blank" rule the moment anyone
    looked at it). The condition below is deliberately "any of the three parts
    is non-blank", not just "``phone`` is non-blank": a handful of rows may
    carry only a prefix or only an extension with the number itself blank
    (nothing on the old model ever enforced the three parts together — see
    ``CompanyContact``'s pre-split docstring), and every part the person
    entered has to survive the move, not just the ones that happen to satisfy
    today's "what counts as a real number" rule.

    USES THE HISTORICAL MODEL (``apps.get_model``), NOT the real
    ``marketing.models`` classes — the standard Django migration discipline,
    and the reason is concrete here: by the time this migration runs, the
    real ``CompanyContact`` class in ``models.py`` no longer even declares
    ``phone_prefix``/``phone``/``phone_ext`` (the next migration removes
    them, and the model file already reflects the end state). Only the
    historical, migration-frozen version of the model still has those three
    columns to read.

    REVERSE IS DELIBERATELY ``RunPython.noop``, exactly the same choice
    ``0006_clear_manual_marketing_data.py`` makes and for a related reason:
    reversing this migration would mean rebuilding the old flat columns from
    however many ``ContactPhone`` rows a contact has picked up SINCE this
    migration ran (including ones created through the new multi-phone form,
    which have no single "the" phone to collapse back into) — a lossy,
    ambiguous operation with no one correct answer, not a real inverse of the
    forward copy. A rollback that needs the pre-migration phone data back is
    a job for a database backup, not for this function.
    """
    CompanyContact = apps.get_model('marketing', 'CompanyContact')
    ContactPhone = apps.get_model('marketing', 'ContactPhone')

    new_rows = []
    for contact in CompanyContact.objects.all():
        prefix = contact.phone_prefix or ""
        number = contact.phone or ""
        ext = contact.phone_ext or ""
        if not (prefix or number or ext):
            continue
        new_rows.append(ContactPhone(
            contact=contact,
            phone_prefix=prefix,
            phone=number,
            phone_ext=ext,
        ))
    if new_rows:
        ContactPhone.objects.bulk_create(new_rows)


class Migration(migrations.Migration):

    dependencies = [
        ('marketing', '0012_contactphone'),
    ]

    operations = [
        migrations.RunPython(
            move_single_phone_to_contact_phone, migrations.RunPython.noop,
        ),
    ]
