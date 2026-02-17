from django import forms
from . import models


class GymForm(forms.ModelForm):
    class Meta:
        model = models.Gym
        fields = ['activity'] # dont include date in the form because it is an auto-generated field
        widgets = {
            'date': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
        }



class CardioForm(forms.ModelForm):
    class Meta:
        model = models.Cardio
        fields = ['activity', 'duration']
        widgets = {
            'date': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
        }



class CommentForm(forms.ModelForm):
    class Meta:
        model = models.Comment
        fields = ['text']