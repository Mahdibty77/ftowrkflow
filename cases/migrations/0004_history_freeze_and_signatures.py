"""2026-07 security pass: freeze timeline actor identity + exported-document
signatory, so a later rename, promotion, or management change can never
rewrite what a historical record already shows.

Purely additive: two new blank-default columns on the existing caseevent
table, and one new table. Nothing here touches workflow state (status,
holder_unit, assigned_to, ...), so no in-flight case is affected by applying
this. Existing CaseEvent rows get actor_name/actor_role_label populated by
the follow-up data migration (0005), not by this one.

NOTE (2026-07-24): the SignatureSnapshot -> CaseForm link uses
db_constraint=False — deliberately no database-level foreign key. The first
deploy attempt failed applying this exact migration because Postgres refused
to add a foreign key referencing cases_caseform (it requires the referenced
column to carry a constraint that table doesn't currently expose for this
purpose on the live database). Rather than alter a table holding live
production data to chase that down, the relationship is enforced by Django
itself instead of by the database. See the field's own comment in
cases/models.py for the full explanation and the one edge case it doesn't
cover (a CaseForm deleted by raw SQL outside the ORM — nothing here does
that).
"""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

import cases.models


class Migration(migrations.Migration):

    dependencies = [
        ("cases", "0003_currencyrate"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="caseevent",
            name="actor_name",
            field=models.CharField(blank=True, max_length=160),
        ),
        migrations.AddField(
            model_name="caseevent",
            name="actor_role_label",
            field=models.CharField(blank=True, max_length=160),
        ),
        migrations.CreateModel(
            name="SignatureSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("signer_name", models.CharField(blank=True, max_length=160)),
                ("signature_image", models.ImageField(blank=True, null=True, upload_to=cases.models.frozen_signature_upload_path)),
                ("stamp_image", models.ImageField(blank=True, null=True, upload_to=cases.models.frozen_stamp_upload_path)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("form", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE,
                                              related_name="signature_snapshot", to="cases.caseform",
                                              db_constraint=False)),
                ("signer_user", models.ForeignKey(blank=True, null=True,
                                                  on_delete=django.db.models.deletion.SET_NULL,
                                                  related_name="frozen_signatures", to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
