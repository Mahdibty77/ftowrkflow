from django.db import migrations, models
import accounts.models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0011_profile_seat_code"),
    ]

    operations = [
        migrations.AddField(
            model_name="profile",
            name="avatar",
            field=models.ImageField(
                blank=True,
                help_text="Circular profile photo shown in the header and My profile.",
                null=True,
                upload_to=accounts.models.avatar_upload_path,
            ),
        ),
    ]
