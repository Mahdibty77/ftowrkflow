"""Adds Profile.language — the per-person platform CHROME language (English
or Persian), read by accounts.middleware.LanguageMiddleware and set from the
new "Language" card in Settings. Purely additive, default="en", so every
existing account keeps rendering in English (today's only interface language)
until its own owner switches it.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0016_profile_unit_add_marketing'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='language',
            field=models.CharField(blank=True, choices=[('en', 'English'), ('fa', 'فارسی')], default='en', max_length=5, verbose_name='Language'),
        ),
    ]
