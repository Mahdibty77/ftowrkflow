"""Root URL configuration for the Foolad Tabar Workflow platform."""
from django.conf import settings
from django.contrib import admin
from django.urls import include, path

from core import views as core_views
from itemcoder import views as itemcoder_views
from itemcoder import engineering_assistant as ea_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("core.urls")),
    # Offline license activation screen (reachable even while the app is locked).
    path("", include("licensing.urls")),
    path("accounts/", include("accounts.urls")),
    path("cases/", include("cases.urls")),
    path("reports/", include("reports.urls")),
    path("people/", include("people.urls")),
    # Item coding / pricing tool + case Build TO/PI bridge.
    path("tool/", include("itemcoder.urls")),
    # The tool's bundled JS calls this absolute path (it ran at site root in the
    # original project); alias it here so live row processing keeps working.
    path("ajax/process-row/", itemcoder_views.process_row_ajax),
    path("ajax/ea-context/", ea_views.assistant_context_ajax),
    path("ajax/ea-options/", ea_views.assistant_options_ajax),
    path("ajax/ea-create-size-item/", ea_views.ea_create_size_item),
    path("app-json/<str:filename>/", itemcoder_views.app_json_resource),
]

# Serve uploaded media (signatures / stamps) behind an authentication check.
#
# This used to be django.conf.urls.static.static(MEDIA_URL, ...), which by
# Django's own design contributes NO url patterns once DEBUG is off — so in
# this deployment (DEBUG is correctly 0 in production) every /media/ request
# has been 404ing since go-live, and was also fully public with no login
# check for anyone running with DEBUG on. core.views.protected_media replaces
# it: it always serves (regardless of DEBUG) and requires the requester to be
# signed in first. See that view's docstring for what it does and does not
# guard today.
urlpatterns += [
    path(f"{settings.MEDIA_URL.strip('/')}/<path:path>",
         core_views.protected_media, name="protected_media"),
]
