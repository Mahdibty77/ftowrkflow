"""URL map for the people app, in four blocks: the directory, the shift pages,
seats, and staff requests.

Two things here are not obvious from the lines themselves. ``person_profile`` is
a second name for ``person_edit`` at a friendlier URL — the hub card and the
post-save redirect reverse that name, and both routes render the same view.
And although almost everything in this app is administrator-only,
``shift_ping``, ``shift_ended``, ``activate_role`` and the whole staff-request
block are reached by ordinary employees; the gate lives on each view, not here.
"""
from django.urls import path

from . import views
from . import views_requests as vreq

app_name = "people"

urlpatterns = [
    path("", views.person_list, name="person_list"),
    path("new/", views.person_create, name="person_create"),
    path("<int:pk>/", views.person_detail, name="person_detail"),
    path("<int:pk>/edit/", views.person_edit, name="person_edit"),
    path("<int:pk>/profile/", views.person_edit, name="person_profile"),
    path("<int:pk>/shift/", views.person_shift, name="person_shift"),
    path("<int:pk>/shift/<int:year>/<int:month>/", views.person_shift_month, name="person_shift_month"),
    path("shift/ping/", views.shift_presence_ping, name="shift_ping"),
    path("shift/ended/", views.shift_ended, name="shift_ended"),
    path("shift/gap/<int:pk>/explain/", views.presence_gap_explain, name="presence_gap_explain"),
    path("<int:pk>/status/", views.person_toggle_status, name="person_toggle_status"),
    path("roles/<int:role_id>/activate/", views.activate_role, name="activate_role"),
    path("<int:pk>/accounts/", views.person_seats, name="person_seats"),
    path("<int:pk>/accounts/assign/", views.seat_assign, name="seat_assign"),
    path("<int:pk>/accounts/reset-password/",
         views.person_reset_password, name="person_reset_password"),
    path("<int:pk>/accounts/<int:seat_id>/release/",
         views.seat_release, name="seat_release"),
    path("<int:pk>/roles/<int:role_id>/release/",
         views.role_release, name="role_release"),
    path("<int:pk>/roles/<int:role_id>/translate/",
         views.role_translate, name="role_translate"),
    path("<int:pk>/roles/<int:role_id>/return/",
         views.role_return, name="role_return"),
    path("<int:pk>/accounts/claim/",
         views.seat_claim, name="seat_claim"),

    # Staff requests (Person-scoped; not CaseForm / seats).
    path("request-types/", vreq.request_types, name="request_types"),
    path("request-types/<int:type_id>/assign/", vreq.request_type_assign, name="request_type_assign"),
    path("<int:pk>/access/", vreq.person_access, name="person_access"),
    path("requests/", vreq.my_requests, name="my_requests"),
    path("requests/overtime/", vreq.overtime_form, name="overtime_form"),
    path("requests/<int:pk>/", vreq.request_detail, name="request_detail"),
    path("requests/gm/overtime/", vreq.gm_overtime_inbox, name="gm_overtime_inbox"),
    path("requests/gm/overtime/<int:pk>/decide/", vreq.gm_overtime_decide, name="gm_overtime_decide"),
    path("requests/gm/gap/<int:pk>/decide/", vreq.gm_presence_gap_decide, name="gm_presence_gap_decide"),
]
