from django.urls import path
from .auth_views import auth_login, auth_logout, auth_change_password, auth_status
from .views.viewports import (list_viewports, current_viewport, viewport_info,
    switch_viewport, create_viewport, delete_viewport, add_years, available_years, is_ready,
    embedding_coverage)
from .views.pipeline import operations_progress, cancel_processing
from .views.vector_data import serve_vector_data
from .views.config import get_config
from .views.evaluation import upload_shapefile, clear_shapefiles, run_evaluation, finish_classifier, download_model
from .views.share import submit_share, list_shares, download_share
from .views.enrolment import create_enrolled_user, list_enrolled_users, disable_enrolled_user

urlpatterns = [
    # Auth
    path('auth/login', auth_login),
    path('auth/logout', auth_logout),
    path('auth/change-password', auth_change_password),
    path('auth/status', auth_status),
    # Viewports
    path('viewports/list', list_viewports),
    path('viewports/current', current_viewport),
    path('viewports/switch', switch_viewport),
    path('viewports/create', create_viewport),
    path('viewports/delete', delete_viewport),
    path('viewports/<str:viewport_name>/info', viewport_info),
    path('viewports/<str:viewport_name>/add-years', add_years),
    path('viewports/<str:viewport_name>/available-years', available_years),
    path('viewports/<str:viewport_name>/is-ready', is_ready),
    path('viewports/<str:viewport_name>/cancel-processing', cancel_processing),
    path('viewports/embedding-coverage', embedding_coverage),
    # Pipeline
    path('operations/progress/<str:operation_id>', operations_progress),
    # Vector data
    path('vector-data/<str:viewport>/<str:year>/<str:filename>', serve_vector_data),
    # Config
    path('config', get_config),
    # Evaluation — proxied to tee-compute
    path('evaluation/upload-shapefile', upload_shapefile),
    path('evaluation/clear-shapefiles', clear_shapefiles),
    path('evaluation/run-large-area', run_evaluation),
    path('evaluation/finish-classifier', finish_classifier),
    path('evaluation/download-model/<str:classifier>', download_model),
    # Label sharing
    path('share/submit', submit_share),
    path('share/list/<str:viewport_name>', list_shares),
    path('share/download/<str:sanitized_email>/<str:viewport_name>', download_share),
    # Enrolment
    path('enrol/create-user', create_enrolled_user),
    path('enrol/list-users', list_enrolled_users),
    path('enrol/disable-user', disable_enrolled_user),
]
