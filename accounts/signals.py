"""Keep a Profile attached to every User.

Only the dedicated ``admin`` username may be a platform administrator.
``is_superuser`` alone never grants ``Profile.is_admin``.

That restriction is deliberate and is not merely about permissions. The flag is
also read as a *filter* elsewhere: ``cases.export_data.unit_manager()`` picks the
signatory whose name and signature go into the Vendor box of every TO/PI with
``is_admin=False``, precisely so a platform administrator is never mistaken for
the manager of a unit. Widening ``is_admin`` to cover Django superusers would
therefore quietly change who signs company documents — a superuser who also sits
in a manager seat would drop out of the pool and new documents would be issued
with no name and no signature at all.

A bootstrap superuser that cannot reach the admin console is a real problem, but
it is a deployment one. It is solved where it is created (``entrypoint.sh``
refuses a name other than ``admin`` rather than producing an account that cannot
administer anything) instead of by changing what the flag means here.
"""
from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Profile


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def ensure_profile(sender, instance, created, **kwargs):
    profile, _was_created = Profile.objects.get_or_create(user=instance)
    want_admin = (instance.username or "").strip().lower() == "admin"
    if profile.is_admin != want_admin:
        profile.is_admin = want_admin
        profile.save(update_fields=["is_admin"])
