# Catch the migration state up with Profile.stamp's help_text.
#
# help_text takes part in field deconstruction, so the wording drifting away
# from what 0005 froze was enough to make `makemigrations --check` dirty on a
# clean checkout — which meant it could not be used as a deployment gate, and
# the next unrelated `makemigrations` would silently carry this change along
# with it. Metadata only: no column is touched and no data moves.
import accounts.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0014_platformconfig_default_daily_hours"),
    ]

    operations = [
        migrations.AlterField(
            model_name="profile",
            name="stamp",
            field=models.ImageField(
                blank=True,
                help_text="Company/personal stamp stamped on approved forms.",
                null=True,
                upload_to=accounts.models.stamp_upload_path,
            ),
        ),
    ]
