import django_filters

from multitreehole.models import Message

class MessageFilter(django_filters.FilterSet):
    class Meta:
        model = Message
        fields = ['published', 'user_identifier']
        order_by = ['timestamp']

class MetaMessageFilter(django_filters.FilterSet):
    class Meta:
        model = Message
        fields = ['service', 'published', 'user_identifier']
        order_by = ['timestamp']
