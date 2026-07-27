from django.db import models
from django.contrib.auth.models import User



# Create your models here.

class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE,related_name='profile')
    bio = models.TextField(blank=True)
    birthday = models.DateField(null = True, blank = True)
    location = models.TextField(null = True, blank = True)
    score = models.IntegerField(default=0)

    # Email notifications: single global on/off switch for ALL emails (comment alerts + monthly
    # reminder). One unsubscribe click sets this to False.
    email_notifications = models.BooleanField(default=True)
    # Timestamp of the last comment-notification email sent to this user; powers the weekly throttle.
    last_comment_email_at = models.DateTimeField(null=True, blank=True)
 
