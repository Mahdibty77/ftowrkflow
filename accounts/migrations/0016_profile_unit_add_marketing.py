"""Record the new MARKETING entry in Profile.unit's choices.

A no-op at the database level: ``choices`` is validation metadata, not schema,
and the column is the same ``varchar(20)`` before and after. It exists so
``makemigrations --check`` stays clean and so the model state Django carries
forward matches accounts.constants.Unit.CHOICES.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0015_alter_profile_stamp'),
    ]

    operations = [
        migrations.AlterField(
            model_name='profile',
            name='unit',
            field=models.CharField(blank=True, choices=[('COMMERCIAL', 'Commercial'), ('TECHNICAL', 'Technical'), ('SUPPLY', 'Supply'), ('MARKETING', 'Marketing')], max_length=20),
        ),
    ]
