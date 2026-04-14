from django.db import models

# Create your models here.

class Group(models.Model):
    name = models.CharField(max_length=255, default = "Private Group")
    size = models.IntegerField(default=0)

    
