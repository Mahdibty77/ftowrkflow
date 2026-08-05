import os

from django.db import migrations, models

import accounts.models


def _copy_legacy_admin_stamp(apps, schema_editor):
    """Migrate the single admin Profile.stamp into Commercial unit stamp."""
    PlatformConfig = apps.get_model("accounts", "PlatformConfig")
    Profile = apps.get_model("accounts", "Profile")
    cfg, _ = PlatformConfig.objects.get_or_create(pk=1)
    if cfg.stamp_commercial:
        return
    for profile in Profile.objects.filter(is_admin=True).order_by("user_id"):
        stamp = getattr(profile, "stamp", None)
        if not stamp or not getattr(stamp, "name", None):
            continue
        try:
            with stamp.open("rb") as fh:
                base = os.path.basename(stamp.name) or "stamp.png"
                cfg.stamp_commercial.save(base, fh, save=True)
            break
        except Exception:
            continue


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0008_profile_gender"),
    ]

    operations = [
        migrations.AddField(
            model_name="platformconfig",
            name="stamp_commercial",
            field=models.FileField(
                blank=True,
                help_text="Commercial unit stamp — used on Proforma (PI) exports.",
                null=True,
                upload_to=accounts.models.stamp_commercial_upload_path,
            ),
        ),
        migrations.AddField(
            model_name="platformconfig",
            name="stamp_technical",
            field=models.FileField(
                blank=True,
                help_text="Technical unit stamp — used on Technical Offer (TO) exports.",
                null=True,
                upload_to=accounts.models.stamp_technical_upload_path,
            ),
        ),
        migrations.AddField(
            model_name="platformconfig",
            name="stamp_supply",
            field=models.FileField(
                blank=True,
                help_text="Supply unit stamp (reserved for future Supply exports).",
                null=True,
                upload_to=accounts.models.stamp_supply_upload_path,
            ),
        ),
        migrations.RunPython(_copy_legacy_admin_stamp, migrations.RunPython.noop),
    ]
