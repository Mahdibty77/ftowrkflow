# Entity/EntityLink -> ClientLabel: the field-scoped "any name, any link"
# directory is replaced by a tag on the SAME cases.Client rows Commercial
# already uses (see marketing/models.py's module docstring).
#
# THIS DOES NOT DROP THE OLD TABLES. A live deployment used the old model for
# real before this migration was written (registered names, links between
# them) and there is no automatic, lossless way to turn "an Entity named X in
# field Y" into "a label on an existing shared Client" — that mapping needs a
# person to decide, not a migration to guess. So `marketing_entity` and
# `marketing_entitylink` are renamed aside (RENAME TABLE, not DROP TABLE —
# every row survives, just outside Django's model layer from here on) rather
# than deleted, and Django's own migration STATE is told the models are gone
# (via `state_operations`) so it matches the current models.py, which no
# longer defines them. A person with database access can still read the
# archived tables directly at any time; nothing here is a request to look at
# or migrate that data automatically.
from django.conf import settings
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cases', '0016_fix_client_missing_constraints'),
        ('marketing', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunSQL(
            sql="ALTER TABLE marketing_entitylink RENAME TO marketing_entitylink_archived_20260831",
            reverse_sql="ALTER TABLE marketing_entitylink_archived_20260831 RENAME TO marketing_entitylink",
            state_operations=[migrations.DeleteModel(name='EntityLink')],
        ),
        migrations.RunSQL(
            sql="ALTER TABLE marketing_entity RENAME TO marketing_entity_archived_20260831",
            reverse_sql="ALTER TABLE marketing_entity_archived_20260831 RENAME TO marketing_entity",
            state_operations=[migrations.DeleteModel(name='Entity')],
        ),
        migrations.CreateModel(
            name='ClientLabel',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('label', models.CharField(choices=[('sponsor', 'سرمایه‌گذار — SPONSOR / INVESTOR'), ('owner', 'کارفرمای اصلی — OWNER / CLIENT'), ('pmt', 'مجری طرح — PMT — PROJECT MGMT TEAM'), ('mc', 'مدیریت طرح — MC / PMC — MGMT CONTRACTOR'), ('licensor', 'لیسانسور — LICENSOR'), ('design', 'مشاور طراح — DESIGN CONSULTANT — FEED / DED'), ('supervision', 'مشاور نظارت — SUPERVISION'), ('c', 'پیمانکار اجرا — C — CONSTRUCTION ONLY'), ('p', 'پیمانکار خرید — P — PROCUREMENT ONLY'), ('pc', 'پیمانکار خرید و اجرا — PC — PROCUREMENT + CONSTRUCTION'), ('epc', 'پیمانکار طرح، خرید و اجرا — EPC — ENG. PROC. CONSTRUCTION'), ('sub', 'پیمانکار جزء — SUBCONTRACTOR')], max_length=32)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('client', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='marketing_labels', to='cases.client')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['label', 'client__name'],
                'constraints': [models.UniqueConstraint(fields=('client', 'label', 'created_by'), name='marketing_clientlabel_unique_per_owner')],
            },
        ),
    ]
