from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0002_profile_plain_password"),
    ]

    operations = [
        migrations.CreateModel(
            name="PlatformConfig",
            fields=[
                ("id", models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
                (
                    "login_welcome_message",
                    models.CharField(
                        default="Let's make a great day",
                        help_text="Shown on the sign-in screen after “Hi, <name>”.",
                        max_length=200,
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Platform configuration",
                "verbose_name_plural": "Platform configuration",
            },
        ),
    ]
