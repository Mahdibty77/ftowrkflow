# Seat index: userN → 001 per Unit+Role pool.

from django.db import migrations, models


def renumber_seat_indexes(apps, schema_editor):
    Profile = apps.get_model("accounts", "Profile")

    def pool_key(p):
        return (
            bool(p.is_admin),
            bool(p.is_general_manager),
            p.unit or "",
            p.role or "",
            p.supply_kind or "",
        )

    groups = {}
    for p in Profile.objects.all().order_by("pk"):
        groups.setdefault(pool_key(p), []).append(p)

    for _key, profiles in groups.items():
        for p in profiles:
            if p.seat_code:
                Profile.objects.filter(pk=p.pk).update(seat_code=None)
        n = 1
        for p in profiles:
            if not (
                p.is_admin
                or p.is_general_manager
                or (p.unit or p.role)
                or p.seat_ready
            ):
                continue
            Profile.objects.filter(pk=p.pk).update(seat_code=f"{n:03d}")
            n += 1


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0012_profile_avatar"),
        ("people", "0007_guarantees_internal_seatlog"),
    ]

    operations = [
        migrations.AlterField(
            model_name="profile",
            name="seat_code",
            field=models.CharField(
                blank=True,
                help_text="Role-local index such as 001. Unique within Unit+Role.",
                max_length=20,
                null=True,
            ),
        ),
        migrations.AddConstraint(
            model_name="profile",
            constraint=models.UniqueConstraint(
                condition=models.Q(seat_code__isnull=False) & ~models.Q(seat_code=""),
                fields=(
                    "seat_code",
                    "unit",
                    "role",
                    "supply_kind",
                    "is_general_manager",
                    "is_admin",
                ),
                name="accounts_profile_seat_index_pool",
            ),
        ),
        migrations.RunPython(renumber_seat_indexes, migrations.RunPython.noop),
    ]
