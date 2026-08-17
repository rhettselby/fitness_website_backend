from django.db import models
from django.contrib.auth.models import User

class WearableConnection (models.Model):

    DEVICE_CHOICES = [('oura', 'Oura Ring'),
                       ('strava', 'Strava'),
                         ('whoop', 'Whoop')]
    
    user = models.ForeignKey(User, on_delete = models.CASCADE)
    device_type = models.CharField(max_length = 75, choices = DEVICE_CHOICES)
    is_active = models.BooleanField(default = True)
    access_token = models.CharField(max_length = 500)
    refresh_token = models.CharField(max_length = 500)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add = True)
    last_sync = models.DateTimeField(null = True, blank = True)
    updated_at = models.DateTimeField(auto_now = True)
    external_user_id = models.CharField(max_length=100, null=True, blank=True)
    # Consecutive times the provider definitively rejected our refresh token. Reset to 0 on
    # a successful refresh; the connection is deactivated once it reaches
    # views.REFRESH_FAILURE_LIMIT. Transient errors (5xx, timeouts) never count.
    refresh_failures = models.PositiveSmallIntegerField(default=0)

    class Meta:
        unique_together = ['user', 'device_type']

    def __str__(self):
        # f-string (formatted string) combined with dash in between
        return f"{self.user.username} - {self.device_type}"




