"""One entry point: /marketing/ is the Marketing workspace (see views.py).

The tabs on that screen are query parameters (``?tab=…``) rather than routes,
so that the tab a person is on survives a reload and can be linked to without
every tab needing a URL of its own before it has any content.
"""
from django.urls import path

from . import views

app_name = "marketing"

urlpatterns = [
    path("", views.home, name="home"),
]
