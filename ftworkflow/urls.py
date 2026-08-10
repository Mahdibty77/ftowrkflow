"""Root URL configuration for the Foolad Tabar Workflow platform.

The map of the whole site. Each mounted app owns its own ``urls.py`` and its own
``app_name`` namespace, so ``{% url 'cases:inbox' %}`` and friends resolve there
rather than here:

    admin/      Django admin — models, and the code-table importer
    (root)      core.urls: the landing router that sends each signed-in user to
                the right starting screen (admin console / dashboard / inbox)
    (root)      licensing.urls: /activate/, the offline licence screen. Mounted
                at root, and reachable while the app is locked, because it is
                the one page that can unlock it — see licensing/middleware.py
    accounts/   sign-in, profiles, units, roles, signatures, impersonation
    cases/      the workflow itself: cases, clients, forms, exports
    reports/    role-aware management dashboards (read-only over cases)
    people/     personnel records, work shifts, staff requests
    tool/       itemcoder: the item-coding / pricing tool and the Build TO/PI
                bridge back into a case

Below that come a handful of aliases at site root. They are NOT a second copy of
anything: each one points at the very same view object the app already exposes
under its own prefix, and they deliberately carry no ``name=`` so that
``{% url %}`` keeps resolving to the namespaced route. See the comment above
them for why they have to exist.
"""
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
    # Root aliases for the item-coding tool.
    #
    # The tool arrived as a standalone project that ran AT site root, and its
    # bundled JavaScript still hard-codes these absolute paths rather than
    # reading a URL out of the DOM — e.g. ``fetch('/ajax/process-row/')`` in
    # itemcoder/static/itemcoder/js/row_processor.js and ``/ajax/ea-context/``,
    # ``/ajax/ea-create-size-item/`` in engineering_assistant.js. Mounting
    # itemcoder under /tool/ moved every one of those endpoints, so without
    # these aliases live row processing and the engineering assistant break.
    #
    # They are same-view aliases and nothing more. The named routes still live
    # in itemcoder/urls.py; teaching the JS to use ``{% url %}`` is what would
    # let this block go away.
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
