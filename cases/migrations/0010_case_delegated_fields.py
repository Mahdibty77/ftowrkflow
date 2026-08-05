from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("cases", "0009_case_expert_display_freeze"),
    ]

    operations = [
        migrations.AddField(
            model_name="case",
            name="is_delegated",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name="case",
            name="delegated_from_seat",
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="delegated_away_cases",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="case",
            name="delegated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
