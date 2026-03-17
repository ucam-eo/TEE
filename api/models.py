from django.db import models
from django.contrib.auth.models import User


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    quota_mb = models.IntegerField(default=2048)

    def __str__(self):
        return f"{self.user.username} ({self.quota_mb} MB)"
