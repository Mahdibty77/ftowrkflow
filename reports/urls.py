"""One entry point: /reports/ dispatches on the viewer's role (see views.py)."""
from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
]
