"""WSGI entry point used by gunicorn / uWSGI in production."""
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ftworkflow.settings")
application = get_wsgi_application()
