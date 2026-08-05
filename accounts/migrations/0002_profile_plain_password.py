from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="profile",
            name="plain_password",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
    ]
