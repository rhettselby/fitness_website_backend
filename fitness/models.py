from django.db import models
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.contrib.contenttypes.fields import GenericForeignKey

# Create your models here.



#parent class

class Workout(models.Model):
    date = models.DateTimeField(auto_now_add = True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)  # owner, CASCADE means workouts are deleted if user is deleted

    class Meta: # Makes Workout an Abstract Base Class
        abstract = True
    

#cardio class, contains date, type, and duration members
class Cardio(Workout):
    activity = models.CharField(max_length = 75)
    duration = models.FloatField(default = 60)
    
    @property
    def model_name(self):
        return self.__class__.__name__
    @property
    def workout_type(self):
        return 'cardio'


#gym class, contains date and type members
class Gym(Workout):
    activity = models.CharField(max_length = 75)

    @property #properties turn a method into an attribute, computed on access
    def model_name(self):
        return self.__class__.__name__
    @property
    def workout_type(self):
        return 'gym'

    
class Comment(models.Model):
    user = models.ForeignKey('auth.User', on_delete=models.CASCADE)
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    workout = GenericForeignKey('content_type', 'object_id')

class Like(models.Model):
    user = models.ForeignKey('auth.User', on_delete=models.CASCADE)
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE) #Using content_type/object_id because Workout is abstract
    object_id = models.PositiveIntegerField()
    workout = GenericForeignKey('content_type', 'object_id')