# Unread / Active vs History for staff requests (requester + reviewer).

from django.db import migrations, models
from django.db.models import F
from django.utils import timezone


def backfill_seen(apps, schema_editor):
    """Treat existing rows as already seen so deploy does not flood badges."""
    StaffRequest = apps.get_model("people", "StaffRequest")
    now = timezone.now()
    # Decided → requester already "knows"; mark both sides seen.
    StaffRequest.objects.filter(
        status__in=["approved", "rejected"],
    ).update(
        requester_seen_at=F("decided_at"),
        reviewer_seen_at=F("decided_at"),
    )
    # Any decided row missing decided_at still needs a stamp.
    StaffRequest.objects.filter(
        status__in=["approved", "rejected"],
        requester_seen_at__isnull=True,
    ).update(requester_seen_at=now, reviewer_seen_at=now)
    # Pending submissions: mark reviewer seen so GM badge stays quiet on upgrade.
    StaffRequest.objects.filter(
        status="submitted",
        reviewer_seen_at__isnull=True,
    ).update(reviewer_seen_at=F("submitted_at"))
    StaffRequest.objects.filter(
        status="submitted",
        reviewer_seen_at__isnull=True,
    ).update(reviewer_seen_at=now)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("people", "0018_staffrequest_request_code"),
    ]

    operations = [
        migrations.AddField(
            model_name="staffrequest",
            name="requester_seen_at",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                help_text="When the requester last opened this request after a status change.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="staffrequest",
            name="reviewer_seen_at",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                help_text="When a reviewer (GM) last opened this request after it was submitted.",
                null=True,
            ),
        ),
        migrations.RunPython(backfill_seen, noop_reverse),
    ]
