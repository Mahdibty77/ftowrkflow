from django.apps import AppConfig


class PeopleConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "people"
    verbose_name = "People & Personnel"

    def ready(self):
        from . import signals
        signals.connect()
