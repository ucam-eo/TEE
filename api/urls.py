from django.urls import path
from .auth_views import auth_login, auth_logout, auth_change_password, auth_status
from .views.viewports import (list_viewports, current_viewport, switch_viewport,
    create_viewport, delete_viewport, add_years, available_years, is_ready)
from .views.pipeline import (
    operations_progress, pipeline_status, cancel_processing)
from .views.vector_data import serve_vector_data
from .views.compute import compute_umap, compute_pca, umap_status, pca_status
from .views.config import get_config

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
    path('viewports/<str:viewport_name>/add-years', add_years),
    path('viewports/<str:viewport_name>/available-years', available_years),
    path('viewports/<str:viewport_name>/is-ready', is_ready),
    path('viewports/<str:viewport_name>/cancel-processing', cancel_processing),
    # Pipeline
    path('operations/progress/<str:operation_id>', operations_progress),
    path('operations/pipeline-status/<str:viewport_name>', pipeline_status),
    # Vector data
    path('vector-data/<str:viewport>/<str:year>/<str:filename>', serve_vector_data),
    # Compute
    path('viewports/<str:viewport_name>/compute-umap', compute_umap),
    path('viewports/<str:viewport_name>/compute-pca', compute_pca),
    path('viewports/<str:viewport_name>/umap-status', umap_status),
    path('viewports/<str:viewport_name>/pca-status', pca_status),
    # Config
    path('config', get_config),
]
