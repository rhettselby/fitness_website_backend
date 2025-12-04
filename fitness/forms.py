from django import forms
from . import models


class GymForm(forms.ModelForm):
    class Meta:
        model = models.Gym
        fields = ['activity'] # dont include date in the form because it is an auto-generated field


class CardioForm(forms.ModelForm):
    class Meta:
        model = models.Cardio
        fields = ['activity', 'duration']


class CommentForm(forms.ModelForm):
    class Meta:
        model = models.Comment
        fields = ['text']