"""One-time backfill of actor_name / actor_role_label on existing CaseEvent rows.

Every row written before migration 0004 has these two fields blank. This
fills them in using each actor's CURRENT name and title — which, as of this
migration, is exactly correct for every row, because no personnel or role
change has happened since the system went live. That is precisely why this
should run as soon as possible after deployment: the longer it waits, the
more likely a role change happens first, at which point any event actually
performed under the *old* title would be backfilled with the new one — an
approximation instead of the exact original truth. Any event logged from
this point forward (via cases.services.log) is captured correctly at the
moment it happens and never needs backfilling.

Reverse is a no-op: there's nothing to undo that would be meaningful (the
alternative is just blank fields again).
"""
from django.db import migrations


def backfill_actor_snapshot(apps, schema_editor):
    CaseEvent = apps.get_model("cases", "CaseEvent")
    Profile = apps.get_model("accounts", "Profile")
    from accounts.constants import Role, Unit  # plain constants, safe in a migration

    # Build {user_id: (name, title)} once, reused for every event by that actor.
    profiles = {
        p.user_id: p
        for p in Profile.objects.select_related("user").all()
    }

    def label_for(user_id):
        profile = profiles.get(user_id)
        if profile is None:
            return "", ""
        user = profile.user
        # NOTE: user is a *historical* model (from apps.get_model), not the
        # real auth.User class — it only has database fields, not Python
        # methods defined on the model class. get_full_name() is a method,
        # not a field, so it does not exist here even though it exists on
        # every real User instance everywhere else in this codebase. Build
        # the same result by hand from the two underlying fields instead.
        full_name = f"{user.first_name} {user.last_name}".strip()
        name = (full_name or user.username or "").strip()
        if profile.is_admin:
            title = "Administrator"
        else:
            parts = [Unit.LABELS.get(profile.unit, ""), Role.LABELS.get(profile.role, "")]
            parts = [p for p in parts if p]
            title = " · ".join(parts) if parts else "Unassigned"
        return name, title

    to_update = []
    qs = (CaseEvent.objects
          .filter(actor_name="", actor_id__isnull=False)
          .only("id", "actor_id"))
    for event in qs.iterator(chunk_size=500):
        name, title = label_for(event.actor_id)
        if not name:
            continue
        event.actor_name = name
        event.actor_role_label = title
        to_update.append(event)
        if len(to_update) >= 500:
            CaseEvent.objects.bulk_update(to_update, ["actor_name", "actor_role_label"])
            to_update = []
    if to_update:
        CaseEvent.objects.bulk_update(to_update, ["actor_name", "actor_role_label"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("cases", "0004_history_freeze_and_signatures"),
        ("accounts", "0005_profile_stamp"),
    ]

    operations = [
        migrations.RunPython(backfill_actor_snapshot, noop_reverse),
    ]
