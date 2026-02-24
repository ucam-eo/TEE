from django.urls import path, include, re_path
from api.views.config import serve_index, serve_static
from api.views.tiles import get_tile, get_bounds, tile_health

urlpatterns = [
    path('', serve_index),
    path('health', include('api.urls_health')),
    path('api/', include('api.urls')),
    path('tiles/health', tile_health),
    path('tiles/<str:viewport>/<str:map_id>/<int:z>/<int:x>/<int:y>.png', get_tile),
    path('bounds/<str:viewport>/<str:map_id>', get_bounds),
    re_path(r'^(?P<path>.+)$', serve_static),
]
