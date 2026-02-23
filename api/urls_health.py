from django.urls import path
from .views.config import health

urlpatterns = [
    path('', health),
]
