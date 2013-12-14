from django import forms
from django.core.exceptions import ValidationError

import json

def validate_json(value):
    try:
        json.loads(value)
    except ValueError, e:
        raise ValidationError(e.message)

class ServiceForm(forms.Form):
    label = forms.CharField(max_length=255)
    params = forms.CharField(widget=forms.Textarea, validators=[validate_json])

class PublishForm(forms.Form):
    text = forms.CharField(widget=forms.Textarea)
