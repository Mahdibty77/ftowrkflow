# Schema step 1 of 3 for the "a contact can have several phone numbers"
# change — see ``marketing/models.py::ContactPhone`` and
# ``marketing/models.py::CompanyContact``'s "THE PHONE NUMBER(S) LIVE ON
# ContactPhone, NOT HERE" section for the full reasoning.
#
# THIS MIGRATION ONLY CREATES THE NEW TABLE. The old
# ``phone_prefix``/``phone``/``phone_ext`` columns on ``CompanyContact`` are
# deliberately left in place for one more migration — see
# ``0013_move_contact_phone_data.py``, the data migration that copies every
# existing single phone number across into one ``ContactPhone`` row before
# ``0014_remove_companycontact_phone_prefix_and_more.py`` drops the old
# columns. Splitting what ``makemigrations`` would otherwise generate as one
# migration (add table + remove columns, back to back) into these three steps
# is what makes the copy a real step in between rather than a race against
# columns that no longer exist.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('marketing', '0011_clear_manual_marketing_data_again'),
    ]

    operations = [
        migrations.CreateModel(
            name='ContactPhone',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('phone_prefix', models.CharField(blank=True, max_length=16)),
                ('phone', models.CharField(blank=True, max_length=32)),
                ('phone_ext', models.CharField(blank=True, max_length=16)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('contact', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='phones', to='marketing.companycontact')),
            ],
            options={
                'ordering': ['created_at', 'pk'],
            },
        ),
    ]
