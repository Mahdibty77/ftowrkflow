from django.db import migrations


def seed_and_bump(apps, schema_editor):
    RequestType = apps.get_model("people", "RequestType")
    Person = apps.get_model("people", "Person")
    PlatformConfig = apps.get_model("accounts", "PlatformConfig")

    RequestType.objects.update_or_create(
        code="presence_gap",
        defaults={
            "title": "Presence gap",
            "description": "System-raised when a shift disconnects longer than the reconnect grace.",
            "icon": "fa-plug-circle-exclamation",
            "sort_order": 15,
            "is_active": True,
        },
    )

    # Only rows still sitting at the OLD default (600s = 10:00) are bumped —
    # a platform/person an administrator already customised to some other
    # value (including deliberately setting it back to 600) is left alone.
    # Mirrors accounts.views._apply_global_daily_hours, which is the normal
    # (non-migration) way this same push already happens.
    PlatformConfig.objects.filter(default_reconnect_grace_seconds=600).update(
        default_reconnect_grace_seconds=900,
    )
    Person.objects.filter(reconnect_grace_seconds=600).update(
        reconnect_grace_seconds=900,
    )


def unseed(apps, schema_editor):
    RequestType = apps.get_model("people", "RequestType")
    RequestType.objects.filter(code="presence_gap").delete()
    # The grace-second bump is not reversed: rows already touched are
    # indistinguishable, after the fact, from ones an administrator set to
    # 900 on purpose — see the forward function's own comment.


class Migration(migrations.Migration):

    dependencies = [
        ("people", "0022_presence_gap_and_grace_bump"),
        ("accounts", "0018_presence_gap_and_grace_bump"),
    ]

    operations = [
        migrations.RunPython(seed_and_bump, unseed),
    ]
