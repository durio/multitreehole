from django.contrib.auth.models import User
from django.core.exceptions import ObjectDoesNotExist, MultipleObjectsReturned
from django.db import models
from django.http import Http404

try:
    from djangoappengine.fields import DbKeyField
    from djangoappengine.db.utils import AncestorKey
    from google.appengine.api.datastore import Key
except ImportError:
    use_ancestor = False
else:
    use_ancestor = True

from djangotoolbox.fields import SetField

import json
import logging
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
    if use_ancestor:
        key = DbKeyField(primary_key=True)

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
        This method returns three values.

        The first is access level:
        * If text is set, returns 'accept', 'moderate', 'throttle' or 'reject'.
        * If text is not set, returns 'accept', 'throttle' or 'reject'.

        The second is user identifier as a string.
        Usually this is user IP with last bits cleared.

        The third is a "confirm" function. Call it after a message is placed
        to confirm access (for throttling).
        '''
        for access in self.get_params().get('access', []):
            access_level, user_identifier, confirm = self.match_access(access, request)
            if access_level != 'reject':
                if text is None or access_level == 'throttle':
                    return access_level, user_identifier, confirm
                else:
                    if 'reject' in access:
                        reject_re = re.compile(access['reject'])
                        if reject_re.search(text):
                            return 'reject', user_identifier, confirm
                    if 'moderate' in access:
                        moderate_re = re.compile(access['moderate'])
                        if moderate_re.search(text):
                            return 'moderate', user_identifier, confirm
                    # access_level should be 'accept' here.
                    return access_level, user_identifier, confirm
        return 'reject', None, lambda obj: True

    def match_access(self, access, request):
        '''
        Returns 'accept', 'throttle' or 'reject',
        plus the user identifier mentioned above.
        '''
        from datetime import datetime, timedelta
        user_identifier = self.extract_user_identifier(access, request)
        confirm = lambda obj: True
        if user_identifier:
            throttle = access.get('throttle')
            if throttle:
                delta = timedelta(seconds=throttle)
                threshold = datetime.now() - delta
                def confirm(obj=None):
                    try:
                        existing = Message.filter_service(self).get(
                            user_identifier=user_identifier,
                            timestamp__gt=threshold,
                        )
                    except ObjectDoesNotExist:
                        # Shouldn't happen
                        if obj:
                            raise Exception('Expected message object is missing when confirming throttling')
                    except MultipleObjectsReturned:
                        return False
                    if obj and existing != obj:
                        raise Exception('Expected message object is not returned when confirming throttling')
                    return True
                if not confirm():
                    return 'throttle', user_identifier, confirm
            return 'accept', user_identifier, confirm
        return 'reject', user_identifier, confirm

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
    # "closed" means this message can't be touched anymore.
    # A "closed" message with no "approved" and "backend" set is an error.
    closed = models.BooleanField(db_index=True)
    # "approved" means whether this message is approved or not in manual review.
    # A "closed", "approved" message without backend set is an error.
    approved = models.NullBooleanField(db_index=True)
    backend = models.ForeignKey(Backend, null=True)
    backend_data = models.TextField()
    if use_ancestor:
        key = DbKeyField(primary_key=True, parent_key_name='parent_key')

    def set_service(self, service):
        '''
        Always use this to update service.
        '''
        if use_ancestor:
            self.parent_key = service.key
        self.service = service

    @classmethod
    def filter_service(cls, service):
        '''
        It's okay to filter on service directly as long as
        strong consistency is not required.
        '''
        if use_ancestor:
            return cls.objects.filter(key=AncestorKey(service.key))
        return cls.objects.filter(service=service)

    @classmethod
    def from_service_id(cls, service, id):
        '''
        Use this with data returned from get_id().
        '''
        if use_ancestor:
            return cls.objects.get(key=Key.from_path(
                cls._meta.db_table, long(id), parent=service.key
            ))
        return cls.objects.get(service=service, pk=id)

    def get_id(self):
        '''
        A pretty ID, but it must be used together with service to do lookup later.
        '''
        if use_ancestor:
            return self.key.id()
        return self.pk
