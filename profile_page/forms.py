from . import models
from django import forms


class EditProfile(forms.ModelForm):
    class Meta:
        model = models.Profile
        fields = ['bio', 'birthday', 'location']

