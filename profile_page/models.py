from django.db import models
from django.contrib.auth.models import User



# Create your models here.

class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE,related_name='profile')
    bio = models.TextField(blank=True)
    birthday = models.TextField(blank = True)
    location = models.TextField(null = True, blank = True)
 
