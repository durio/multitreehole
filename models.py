from django.contrib.auth.models import User
from django.db import models
from django.http import Http404

from djangotoolbox.fields import SetField

import json
import re

class Backend(models.Model):
    path = models.CharField(max_length=255)
    params = models.TextField()

class Service(models.Model):
    SLUG_RE = re.compile(r'^([a-zA-Z0-9]|[a-zA-Z0-9][a-zA-Z0-9\-]*[a-zA-Z0-9])$')
    slug = models.SlugField(unique=True, db_index=True)
    label = models.CharField(max_length=255)
    backend = models.ForeignKey(Backend, null=True)
    params = models.TextField()
    owners = SetField(models.ForeignKey(User))

    @classmethod
    def split_request_host(cls, request):
        host = request.META['HTTP_HOST']
        pieces = host.split('.', 1)
        if not cls.SLUG_RE.match(pieces[0]):
            raise Http404
        if len(pieces) < 2:
            pieces.append('')
        else:
            pieces[1] = '.' + pieces[1]
        return tuple(pieces)

    @classmethod
    def get_from_request(cls, request):
        return cls.objects.get(slug=cls.split_request_host(request)[0])

    @classmethod
    def new_from_request(cls, request):
        return cls(slug=cls.split_request_host(request)[0])

    @classmethod
    def build_host(cls, slug, request):
        return slug + cls.split_request_host(request)[1]

    def get_host(self, request):
        return self.build_host(self.slug, request)

    def get_params(self):
        if not hasattr(self, 'params_data'):
            self.params_data = json.loads(self.params)
        return self.params_data

    def check_access(self, request, text=None):
        '''
        This method returns two values.

        The first is access level:
        * If text is set, returns 'accept', 'moderate', 'throttle' or 'reject'.
        * If text is not set, returns 'accept', 'throttle' or 'reject'.

        The second is user identifier as a string.
        Usually this is user IP with last bits cleared.
        '''
        for access in self.get_params().get('access', []):
            access_level, user_identifier = self.match_access(access, request)
            if access_level != 'reject':
                if text is None or access_level == 'throttle':
                    return access_level, user_identifier
                else:
                    if 'reject' in access:
                        reject_re = re.compile(access['reject'])
                        if reject_re.search(text):
                            return 'reject', user_identifier
                    if 'moderate' in access:
                        moderate_re = re.compile(access['moderate'])
                        if moderate_re.search(text):
                            return 'moderate', user_identifier
                    # access_level should be 'accept' here.
                    return access_level, user_identifier
        return 'reject', None

    def match_access(self, access, request):
        '''
        Returns 'accept', 'throttle' or 'reject',
        plus the user identifier mentioned above.
        '''
        from datetime import datetime, timedelta
        user_identifier = self.extract_user_identifier(access, request)
        if user_identifier:
            throttle = access.get('throttle')
            if throttle:
                delta = timedelta(seconds=throttle)
                threshold = datetime.now() - delta
                if Message.objects.filter(service=self,
                    user_identifier=user_identifier,
                    timestamp__gt=threshold,
                ).exists():
                    return 'throttle', user_identifier
            return 'accept', user_identifier
        return 'reject', user_identifier

    def extract_user_identifier(self, access, request):
        import ipaddr
        try:
            network = ipaddr.IPNetwork(access.get('network'))
        except ValueError:
            network = None
        address = ipaddr.IPAddress(request.META['REMOTE_ADDR'])
        if network and address in network:
            subnet = ipaddr.IPNetwork(address).supernet(access.get('suffixlen', 0))
            return str(subnet.network)
        return None

    def __unicode__(self):
        return self.label

class Message(models.Model):
    service = models.ForeignKey(Service, db_index=True)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    user_identifier = models.CharField(max_length=255, db_index=True)
    text = models.TextField()
    closed = models.BooleanField(db_index=True)
    backend = models.ForeignKey(Backend, null=True)
    backend_data = models.TextField()
