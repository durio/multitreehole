import django_filters

from multitreehole.models import Message

class MessageFilter(django_filters.FilterSet):
    class Meta:
        model = Message
        fields = ['closed', 'user_identifier']
        order_by = ['-timestamp', 'timestamp']
