from django import forms

from multitreehole.forms import validate_json

class BasicBackendForm(forms.Form):
    backend_params = forms.CharField(widget=forms.Textarea, validators=[validate_json])

    def to_json(self):
        return self.cleaned_data['backend_params']
