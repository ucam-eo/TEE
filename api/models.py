from django.db import models
from django.contrib.auth.models import User


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    quota_mb = models.IntegerField(default=2048)
    can_enrol = models.BooleanField(default=False)
    created_by = models.ForeignKey(User, null=True, blank=True,
                                   on_delete=models.SET_NULL,
                                   related_name='enrolled_users')

    def __str__(self):
        return f"{self.user.username} ({self.quota_mb} MB, enrol={self.can_enrol})"
