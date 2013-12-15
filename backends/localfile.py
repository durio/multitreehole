from django import forms
from django.utils.translation import ugettext_lazy as _

import json

class LocalFileBackendForm(forms.Form):
    file_name = forms.CharField()

    def to_json(self):
        return json.dumps({
            'file-name': self.cleaned_data['file_name'],
        })

class LocalFileBackend(object):
    slug = 'local-file'
    label = _('Local file')
    form_class = LocalFileBackendForm

    def make_client(self, pk, params):
        params = json.loads(params)
        client = LocalFileClient(pk, params['file-name'])
        return client

class LocalFileClient(object):
    def __init__(self, pk, file_name):
        self.pk = pk
        self.file_name = file_name

    def make_message(self, text):
        return LocalFileMessage(self, text)

class LocalFileMessage(object):
    def __init__(self, client, text):
        self.client = client
        self.text = text

    def publish(self, POST, FILES):
        file_obj = file.__new__(file, self.client.file_name, 'a')
        try:
            from google.appengine.tools.dev_appserver import FakeFile
        except ImportError:
            pass
        else:
            file_obj = super(file, file_obj)
        file_obj.__init__(self.client.file_name, 'a')
        print >>file_obj, self.text
        file_obj.close()
        return {'data': ''}
