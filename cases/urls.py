from django.urls import path

from . import views

app_name = "cases"

urlpatterns = [
    path("", views.inbox, name="inbox"),
    path("archive/", views.archive, name="archive"),
    path("new/", views.case_create, name="case_create"),
    path("preview-excel/", views.preview_excel, name="preview_excel"),
    path("<int:pk>/", views.case_detail, name="case_detail"),
    path("<int:pk>/edit-items/", views.edit_items, name="edit_items"),
    path("<int:pk>/transition/", views.transition, name="transition"),
    path("<int:pk>/currency-conversion/log/", views.log_currency_conversion, name="log_currency_conversion"),
    path("<int:pk>/export/<str:form_kind>/pdf/confirm/", views.export_form_pdf_confirm, name="export_form_pdf_confirm"),
    path("<int:pk>/export/<str:form_kind>/html/confirm/", views.export_form_html_confirm, name="export_form_html_confirm"),
    path("<int:pk>/export/<str:form_kind>/<str:fmt>/", views.export_form, name="export_form"),

    # Commercial master data
    path("clients/", views.client_list, name="client_list"),
    path("clients/add/", views.client_add, name="client_add"),
    path("clients/<int:pk>/rename/", views.client_rename, name="client_rename"),
    path("clients/<int:pk>/delete/", views.client_delete, name="client_delete"),
    path("clients/upload/", views.client_upload, name="client_upload"),
    path("clients/wipe/", views.client_wipe, name="client_wipe"),
    path("clients/lookup/", views.client_lookup, name="client_lookup"),

    path("master-data/", views.master_data_hub, name="master_data_hub"),
    path("fx-rates/", views.fx_rates, name="fx_rates"),
    path("fx-rates/add/", views.fx_rate_add, name="fx_rate_add"),
    path("fx-rates/update-all/", views.fx_rate_update_all, name="fx_rate_update_all"),
    path("fx-rates/<int:pk>/update/", views.fx_rate_update, name="fx_rate_update"),
    path("fx-rates/<int:pk>/delete/", views.fx_rate_delete, name="fx_rate_delete"),
    path("fx-rates/api/", views.fx_rates_api, name="fx_rates_api"),

    path("expert-codes/", views.expert_code_list, name="expert_code_list"),
    path("expert-codes/add/", views.expert_code_add, name="expert_code_add"),
    path("expert-codes/<int:pk>/edit/", views.expert_code_edit, name="expert_code_edit"),
    path("expert-codes/upload/", views.expert_code_upload, name="expert_code_upload"),
]
