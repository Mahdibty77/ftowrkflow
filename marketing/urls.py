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
    directory/<pk>/reports/add/     report, step 1    -> marketing:report_add
    directory/<pk>/reports/create/  report, step 2    -> marketing:report_create
    directory/<pk>/reminders/add/   set a reminder    -> marketing:reminder_add
    reminders/                      YOUR own list     -> marketing:reminder_list
    reminders/<id>/time/            set a new time    -> marketing:reminder_retime
    reminders/<id>/done/            mark dealt with   -> marketing:reminder_done

THE REMINDER LIST IS NOT UNDER ``directory/``, AND THAT IS THE POINT OF ITS
ADDRESS. Everything under ``directory/`` is about a COMPANY and carries that
company's id; a person's reminder list is about the PERSON — it spans every
company they have set one on — so it hangs off the section root instead, with no
id in the path at all. Whose list it is comes from the session and can never
come from the URL, which is the whole of the visibility rule (see
``marketing/models.py::Reminder``). SETTING one is the other way round: it is
reached from one company's page and is about that company, so it lives under
``directory/<pk>/`` beside the contact and report flows it is modelled on.

The two mutating reminder routes are POST-only (see ``views.reminder_retime`` /
``views.reminder_done``) and carry the reminder's own id in the path, exactly as
the contact-removal route does — the id a mutation is about belongs in the
address, not in a body parameter the view might forget to re-check. Neither
route carries an owner: the signed-in person IS the owner, and a route that
could name a different one would be a route that has to be checked for it.

The removal route is POST-only (see ``views.contact_remove``) and carries the
contact's own id in the path rather than in the body, so the two ids a removal
is about — which company, which person — are both part of the address and
neither can be swapped by a body parameter the view forgot to re-check.

THE TWO REPORT ROUTES ARE THE OWNER'S TWO STEPS, and they are two URLs for the
same reason the archive and a case detail page are: ``report_add`` is a screen a
reader opens, reloads and comes back to (it only asks which case, if any, the
report is about), while ``report_create`` is the write and is POST-only (see
``views.report_create``). A single URL switching on the request method would
have made the writing step reachable by GET, which is precisely what
``contact_remove`` is shaped to avoid.

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
    path("directory/<int:pk>/reports/add/", views.report_add, name="report_add"),
    path("directory/<int:pk>/reports/create/",
         views.report_create, name="report_create"),
    path("directory/<int:pk>/reminders/add/",
         views.reminder_add, name="reminder_add"),
    path("reminders/", views.reminder_list, name="reminder_list"),
    path("reminders/<int:reminder_id>/time/",
         views.reminder_retime, name="reminder_retime"),
    path("reminders/<int:reminder_id>/done/",
         views.reminder_done, name="reminder_done"),
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
