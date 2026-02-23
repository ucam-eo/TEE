from django.urls import path, include, re_path
from api.views.config import serve_index, serve_static

urlpatterns = [
    path('', serve_index),
    path('health', include('api.urls_health')),
    path('api/', include('api.urls')),
    re_path(r'^(?P<path>.+)$', serve_static),
]
