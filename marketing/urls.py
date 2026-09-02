"""Two entry points: the Marketing workspace, and the company directory.

``/marketing/`` is the workspace — the role chart (see ``views.home``). That
screen used to carry a tab strip, driven by a ``?tab=…`` query parameter
rather than a route of its own per tab. It has one panel now — the role chart
— so there is no ``tab`` parameter any more and nothing links to one; an
unknown query string on this URL is simply ignored, as it always was. The
only parameter the page still reads is the optional ``?case=`` deep link (see
``views.home``).

``/marketing/directory/`` is the company directory — the section that replaced
the "Companies" tab that screen used to carry (see ``views.py``'s module
docstring). REAL ROUTES, one per screen, not a ``?tab=`` on an existing page:
a company detail page is a destination a reader links to, bookmarks and comes
back to, which is exactly what the tab parameter could never be, and it is why
the case archive and the case detail page are two URLs rather than one screen
with a switch on it. This section is modelled on that pair, so its URLs are
shaped the same way:

    directory/                      the list          -> marketing:directory
    directory/<pk>/                 one company       -> marketing:company_detail
    directory/<pk>/contacts/add/    add a contact     -> marketing:contact_add
    directory/<pk>/contacts/<id>/remove/
                                    remove a contact  -> marketing:contact_remove

The removal route is POST-only (see ``views.contact_remove``) and carries the
contact's own id in the path rather than in the body, so the two ids a removal
is about — which company, which person — are both part of the address and
neither can be swapped by a body parameter the view forgot to re-check.

The ``<int:pk>`` is a ``cases.Client`` id — the SAME shared directory the chart
and Commercial both use, not a Marketing-only mirror of it (see
``marketing/models.py``'s module docstring), so a company's id means the same
thing here as it does everywhere else in the platform.

Everything under "entities/" is JSON, called by
``marketing/static/marketing/js/chart_interact.js``.
"""
from django.urls import path

from . import views

app_name = "marketing"

urlpatterns = [
    path("", views.home, name="home"),
    path("directory/", views.directory, name="directory"),
    path("directory/<int:pk>/", views.company_detail, name="company_detail"),
    path("directory/<int:pk>/contacts/add/", views.contact_add, name="contact_add"),
    path("directory/<int:pk>/contacts/<int:contact_id>/remove/",
         views.contact_remove, name="contact_remove"),
    path("entities/clients/search/", views.client_search, name="client_search"),
    path("entities/clients/create/", views.client_create, name="client_create"),
    path("entities/clients/connections/", views.client_connections, name="client_connections"),
    path("entities/connection/toggle/", views.connection_toggle, name="connection_toggle"),
    path("entities/label/companies/", views.label_companies, name="label_companies"),
    path("entities/label/toggle/", views.label_toggle, name="label_toggle"),
    # There was a ``entities/us/connections/`` route here, pointing at a
    # ``views.us_connections``. Both are gone, along with the
    # ``usConnectionsUrl`` key ``marketing/templates/marketing/home.html`` used
    # to hand the chart: no client ever read that key, so nothing could reach
    # the route. The "us" card is served by ``client_connections`` and
    # ``all_cases_search`` instead. (``services.us_connections`` still exists —
    # ``views.home`` counts it for the card's badge.)
    path("entities/clients/case-counts/", views.client_case_counts, name="client_case_counts"),
    path("entities/cases/search/", views.all_cases_search, name="all_cases_search"),
]
