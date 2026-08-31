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
    path("entities/clients/search/", views.client_search, name="client_search"),
    path("entities/clients/create/", views.client_create, name="client_create"),
    path("entities/clients/connections/", views.client_connections, name="client_connections"),
    path("entities/label/companies/", views.label_companies, name="label_companies"),
    path("entities/label/toggle/", views.label_toggle, name="label_toggle"),
    path("entities/us/connections/", views.us_connections, name="us_connections"),
]
