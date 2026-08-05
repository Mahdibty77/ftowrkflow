from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.CaptureLoginView.as_view(), name="login"),
    path("login/check/", views.login_check, name="login_check"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("force-password-change/", views.force_password_change, name="force_password_change"),

    path("console/", views.admin_console, name="admin_console"),
    path("users/", views.user_list, name="user_list"),
    path("users/new/", views.user_create, name="user_create"),
    path("users/<int:pk>/edit/", views.user_edit, name="user_edit"),
    path("users/<int:pk>/assign/", views.seat_assign, name="seat_assign"),
    path("users/<int:pk>/translate/", views.seat_translate, name="seat_translate"),
    path("users/<int:pk>/return/", views.seat_return, name="seat_return"),
    path("users/<int:pk>/close/", views.seat_close, name="seat_close"),
    path("users/<int:pk>/delegate/", views.seat_delegate, name="seat_delegate"),
    path("users/<int:pk>/history/", views.seat_history, name="seat_history"),
    path("users/<int:pk>/toggle-active/", views.user_toggle_active, name="user_toggle_active"),
    path("users/<int:pk>/reset-password/", views.user_reset_password, name="user_reset_password"),
    path("users/<int:pk>/impersonate/", views.impersonate_start, name="impersonate_start"),
    path("impersonate/stop/", views.impersonate_stop, name="impersonate_stop"),
    path("profile/", views.my_profile, name="my_profile"),
    path("settings/", views.settings_page, name="settings"),

    path("backups/", views.backup_console, name="backup_console"),
    path("backups/download/<path:name>/", views.backup_download, name="backup_download"),
]
