"""Mounted at site root by ftworkflow/urls.py, so /activate/ stays reachable
while the licence gate has locked everything else."""
from django.urls import path

from . import views

app_name = "licensing"

urlpatterns = [
    path("activate/", views.activate, name="activate"),
]
