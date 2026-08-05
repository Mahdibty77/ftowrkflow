from django.urls import path
from . import views
from . import bridge
from . import data_admin
from . import engineering_assistant

urlpatterns = [
    path('upload/', views.upload_excel, name='upload_excel'),
    path('ajax/process-row/', views.process_row_ajax, name='process_row_ajax'),
    path('ajax/ea-context/', engineering_assistant.assistant_context_ajax, name='ea_context'),
    path('ajax/ea-options/', engineering_assistant.assistant_options_ajax, name='ea_options'),
    path('ajax/ea-create-size-item/', engineering_assistant.ea_create_size_item, name='ea_create_size_item'),
    path('app-json/<str:filename>/', views.app_json_resource, name='app_json_resource'),
    # Case bridge: open the tool seeded from a case, and save the grid back.
    path('case/<int:case_id>/<str:kind>/', bridge.tool_for_case, name='tool_for_case'),
    path('case/<int:case_id>/<str:kind>/status/', bridge.tool_for_case_status, name='tool_for_case_status'),
    path('case/<int:case_id>/<str:kind>/save/', bridge.save_from_tool, name='tool_save'),
    path('prices/', bridge.tool_prices, name='tool_prices'),
    path('features/', bridge.tool_features, name='tool_features'),
    # Admin data management (admin-only).
    path('admin/data/', data_admin.dm_home, name='dm_home'),
    path('admin/data/reference/', data_admin.dm_technical_home, name='dm_technical_home'),
    path('admin/data/ea-log/', data_admin.dm_ea_item_log, name='dm_ea_item_log'),
    path('admin/data/code/upload/', data_admin.dm_code_upload, name='dm_code_upload'),
    path('admin/data/code/confirm/', data_admin.dm_code_confirm, name='dm_code_confirm'),
    path('admin/data/code/import-status/<str:job_id>/', data_admin.dm_code_import_status, name='dm_code_import_status'),
    path('admin/data/code/<str:group>/rows/', data_admin.dm_code_rows, name='dm_code_rows'),
    path('admin/data/code/<str:group>/rows-data/', data_admin.dm_code_rows_api, name='dm_code_rows_api'),
    path('admin/data/prices/', data_admin.dm_price_lists, name='dm_price_lists'),
    path('admin/data/prices/<int:pk>/upload/', data_admin.dm_price_upload, name='dm_price_upload'),
    path('admin/data/prices/<int:pk>/workspace/', data_admin.dm_price_workspace, name='dm_price_workspace'),
    path('admin/data/prices/<int:pk>/rows/', data_admin.dm_price_rows_api, name='dm_price_rows_api'),
    path('admin/data/prices/<int:pk>/apply/', data_admin.dm_price_apply, name='dm_price_apply'),
    path('admin/data/prices/<int:pk>/set-one/', data_admin.dm_price_set_one, name='dm_price_set_one'),
    # Feature schema (attributes + value/codes) and single-item builder.
    path('admin/data/features/<str:group>/', data_admin.dm_features, name='dm_features'),
    path('admin/data/features/<str:group>/<str:feature>/', data_admin.dm_feature_values, name='dm_feature_values'),
    # Cascading-rules file: per-group upload / download.
    path('admin/data/rules/<str:group>/upload/', data_admin.dm_rules_upload, name='dm_rules_upload'),
    path('admin/data/rules/<str:group>/download/', data_admin.dm_rules_download, name='dm_rules_download'),
    # Offer file: per-group interactive builder, upload / download, and APIs.
    path('admin/data/offer/<str:group>/', data_admin.dm_offer, name='dm_offer'),
    path('admin/data/offer/<str:group>/upload/', data_admin.dm_offer_upload, name='dm_offer_upload'),
    path('admin/data/offer/<str:group>/download/', data_admin.dm_offer_download, name='dm_offer_download'),
    path('admin/data/offer/<str:group>/values/', data_admin.dm_offer_values_api, name='dm_offer_values_api'),
    path('admin/data/offer/<str:group>/save/', data_admin.dm_offer_save_api, name='dm_offer_save_api'),
    # Rebuilt offer builder (searchable pickers + AND conditions).
    path('admin/data/offer/<str:group>/feature-values/', data_admin.dm_offer_feature_values_api, name='dm_offer_feature_values_api'),
    path('admin/data/offer/<str:group>/conditions/', data_admin.dm_offer_conditions_api, name='dm_offer_conditions_api'),
    path('admin/data/offer/<str:group>/conditions/save/', data_admin.dm_offer_conditions_save_api, name='dm_offer_conditions_save_api'),
    path('admin/data/items/<str:group>/new/', data_admin.dm_item_new, name='dm_item_new'),
    path('admin/data/items/<str:group>/seq/', data_admin.dm_item_seq_api, name='dm_item_seq_api'),
    path('admin/data/items/<str:group>/options/', data_admin.dm_item_options_api, name='dm_item_options_api'),
    path('admin/data/code/<str:group>/distinct/', data_admin.dm_code_distinct_api, name='dm_code_distinct_api'),
]
