# Remove Leave / Purchase request types (Overtime-only phase).
from django.db import migrations


def purge_leave_purchase(apps, schema_editor):
    RequestType = apps.get_model("people", "RequestType")
    StaffRequest = apps.get_model("people", "StaffRequest")
    PersonRequestAccess = apps.get_model("people", "PersonRequestAccess")
    codes = ["leave", "purchase"]
    type_ids = list(
        RequestType.objects.filter(code__in=codes).values_list("pk", flat=True)
    )
    if type_ids:
        StaffRequest.objects.filter(request_type_id__in=type_ids).delete()
        PersonRequestAccess.objects.filter(request_type_id__in=type_ids).delete()
        RequestType.objects.filter(pk__in=type_ids).delete()


def noop_reverse(apps, schema_editor):
    # Leave/Purchase intentionally not restored.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("people", "0016_staff_requests"),
    ]

    operations = [
        migrations.RunPython(purge_leave_purchase, noop_reverse),
    ]
