import os

from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tee_project.settings')

app = Celery('tee')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()
