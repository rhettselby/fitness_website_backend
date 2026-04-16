from django.db import models
from django.contrib.auth.models import User

# Create your models here.

class FitnessGroup(models.Model):
    name = models.CharField(max_length=255, default = "Private Group")
    members = models.ManyToManyField(User, related_name = "fitness_group", blank=True)
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name = "owned_groups", default = 1)
    motto = models.CharField(max_length=255, default = None, null=True)


    

    
