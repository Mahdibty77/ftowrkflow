from django.apps import AppConfig


class ItemcoderConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'itemcoder'

    def ready(self):
        # Warm JSON/CSV/code indexes once when the web server starts. This moves
        # the heavy first-request cost out of live typing/upload processing.
        try:
            from .startup_warmup import should_warm_on_ready, warm_all_runtime_caches
            if should_warm_on_ready():
                warm_all_runtime_caches()
        except Exception:
            # Startup must never fail because an optional CSV/config is missing.
            pass
