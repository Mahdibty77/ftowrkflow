"""Site root. Only the landing router lives here; the authenticated /media/
route that core also serves is mounted by ftworkflow/urls.py instead, because
its path comes from settings.MEDIA_URL.

(A ``marketing/`` placeholder briefly lived here while the Marketing section
had no app of its own. It has been removed: ``marketing.urls`` now owns that
path from ftworkflow/urls.py. Nothing may be added back at this prefix — an
entry here would shadow the app, because ``core.urls`` is included at "" and
therefore matched before the app's own mount.)"""
from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
]
