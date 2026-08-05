"""Keep a Profile attached to every User.

Only the dedicated ``admin`` username may be a platform administrator.
``is_superuser`` alone never grants ``Profile.is_admin``.
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
