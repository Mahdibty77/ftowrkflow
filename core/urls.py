"""Site root. Only the landing router lives here; the authenticated /media/
route that core also serves is mounted by ftworkflow/urls.py instead, because
its path comes from settings.MEDIA_URL."""
from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
]
