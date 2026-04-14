from django.db import models
from fitness_groups.models import FitnessGroup

class Users (models.Model):
    title = models.CharField(max_length = 75)
    date = models.DateTimeField(auto_now_add = True)
    slug = models.SlugField()
    body = models.TextField()
    score = models.IntegerField(default=0)
    groups = models.ManyToManyField(FitnessGroup, related_name='members')

    def __str__(self):
        return self.title







