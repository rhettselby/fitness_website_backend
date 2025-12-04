from django.db import models

class Users (models.Model):
    title = models.CharField(max_length = 75)
    date = models.DateTimeField(auto_now_add = True)
    slug = models.SlugField()
    body = models.TextField()

    def __str__(self):
        return self.title







