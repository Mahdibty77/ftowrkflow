"""Adds Profile.gender — used only to choose Mr./Ms. before a signer's last
name on exported documents. Purely additive, blank-default, no existing
account is affected until someone sets it via the user-edit screen.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0007_clear_plain_password"),
    ]

    operations = [
        migrations.AddField(
            model_name="profile",
            name="gender",
            field=models.CharField(
                blank=True,
                choices=[("MALE", "Male"), ("FEMALE", "Female")],
                max_length=10,
            ),
        ),
    ]
