from django import forms
from django.conf import settings
from django.core.cache import cache
from django.forms.util import ErrorList
from django.utils.encoding import smart_bytes
from django.utils.html import format_html
from django.utils.translation import ugettext_lazy as _

from multitreehole.backends import BasicBackendForm

import json
import mechanize
import re

class RenrenBackend(object):
    slug = 'renren'
    label = _('Renren')
    form_class = BasicBackendForm

    def make_client(self, pk, params):
        params = json.loads(params)
        client = RenrenClient(pk, params['username'], params['password'], params['base-url'])
        return client

class RenrenClient(object):
    URL_CACHE_KEY = 'multitreehole_backend_renren_url'

    def __init__(self, pk, username, password, base_url):
        self.pk = pk
        self.username = username
        self.password = password
        self.base_url = base_url
        self.url = None

    def load_cache(self):
        self.url = cache.get(self.URL_CACHE_KEY)

    def save_cache(self):
        cache.set(self.URL_CACHE_KEY, self.url,
            getattr(settings, 'MULTITREEHOLE_BACKEND_RENREN_LOGIN_CACHE_TIMEOUT'),
        )

    def make_browser(self):
        browser = mechanize.Browser()
        browser.set_handle_robots(False)
        return browser

    def get_url(self, force=False, captcha_key=None, captcha=None):
        self.load_cache()
        if (self.url is None or force) and captcha_key and captcha:
            try:
                browser = self.make_browser()
                browser.open(self.base_url)
                browser.select_form(nr=0)
                browser.set_all_readonly(False)
                browser['email'] = smart_bytes(self.username)
                browser['password'] = smart_bytes(self.password)
                browser['verifykey'] = smart_bytes(captcha_key)
                browser['verifycode'] = smart_bytes(captcha)
                browser.submit()
                url = browser.find_link(url_regex=re.compile(r'.*/profile\.do\?')).url
            except Exception:
                return None
            else:
                self.url = url
                self.save_cache()
        return self.url

    def get_captcha_info(self):
        import random
        try:
            browser = self.make_browser()
            browser.open(self.base_url)
            browser.select_form(nr=0)
            key = browser['verifykey']
            url = self.base_url + '/rndimg_wap?post=_REQUESTFRIEND_%s&rnd=%f' % (key, random.random())
            return key, url
        except Exception:
            return None, None

    def make_message(self, text):
        return RenrenMessage(self, text)

class RenrenMessage(object):
    def __init__(self, client, text):
        self.client = client
        self.text = text

    def publish(self, POST, FILES):
        url = self.client.get_url()
        form = None

        def mark_captcha_error(form):
            form._errors['captcha'] = ErrorList([_('Renren login error. Incorrect captcha?')])

        def make_form():
            form = RenrenLoginCaptchaForm(POST, FILES)
            form.fields['captcha_key'].widget.client = self.client
            return form

        if url is None:
            form = make_form()
            if not form.is_valid():
                return {'forms': [form]}
            url = self.client.get_url(
                captcha_key=form.cleaned_data['captcha_key'],
                captcha=form.cleaned_data['captcha'],
            )
            if url is None:
                mark_captcha_error(form)
                return {'forms': [form]}
        # url is not None now.

        def try_submit(url):
            browser = self.client.make_browser()
            browser.open(url)
            browser.select_form(nr=0)
            browser['status'] = smart_bytes(self.text)
            browser.submit()
            if '%E7%8A%B6%E6%80%81%E5%8F%91%E5%B8%83%E6%88%90%E5%8A%9F' not in browser.geturl():
                raise Exception

        try:
            try_submit(url)
        except Exception:
            # XXX: sometimes this is just a publishing error. Not a login error.
            if not form:
                form = make_form()
                if not form.is_valid():
                    return {'forms': [form]}
            url = self.client.get_url(force=True,
                captcha_key=form.cleaned_data['captcha_key'],
                captcha=form.cleaned_data['captcha'],
            )
            if url is None:
                mark_captcha_error(form)
                return {'forms': [form]}
            try:
                try_submit(url)
            except Exception:
                return {'error': ErrorList([_('Renren publishing error. Message rejected there?')])}

        return {'data': ''}

class RenrenLoginCaptchaWidget(forms.HiddenInput):
    is_hidden = False

    def render(self, name, value, attrs=None):
        import random
        captcha_key, captcha_url = self.client.get_captcha_info()
        html = format_html('<img src="{0}">', captcha_url)
        return html + super(RenrenLoginCaptchaWidget, self).render(name, captcha_key, attrs)

class RenrenLoginCaptchaForm(forms.Form):
    captcha_key = forms.CharField(widget=RenrenLoginCaptchaWidget, required=False)
    captcha = forms.CharField()
