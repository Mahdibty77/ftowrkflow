"""ASGI entry point (for async servers, optional)."""
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ftworkflow.settings")
application = get_asgi_application()
