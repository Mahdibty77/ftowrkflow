# Schema step 3 of 3 — drop the old single-phone columns now that
# ``0013_move_contact_phone_data.py`` has copied every existing value across
# onto its own ``ContactPhone`` row. See ``0012_contactphone.py``'s module
# comment for why this is a separate, later step rather than bundled with the
# table creation the way ``makemigrations`` would generate it unprompted.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('marketing', '0013_move_contact_phone_data'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='companycontact',
            name='phone',
        ),
        migrations.RemoveField(
            model_name='companycontact',
            name='phone_ext',
        ),
        migrations.RemoveField(
            model_name='companycontact',
            name='phone_prefix',
        ),
    ]
