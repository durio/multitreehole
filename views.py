from django.conf import settings
from django.contrib.auth.decorators import login_required as normal_login_required
from django.core.exceptions import ObjectDoesNotExist
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.core.urlresolvers import reverse
from django.forms.util import ErrorList
from django.http import Http404, HttpResponseRedirect, HttpResponseForbidden
from django.shortcuts import render_to_response
from django.template import RequestContext
from django.utils.decorators import method_decorator
from django.utils.translation import ugettext_lazy as _
from django.views.generic import ListView
from django.views.generic.base import View, TemplateResponseMixin

from multitreehole.filters import MessageFilter
from multitreehole.forms import ServiceForm, PublishForm
from multitreehole.models import Backend, Service, Message
from multitreehole.utils import load_backend, get_backends, get_backend_or_404

import logging
import traceback

def service_required(view):
    def func(request, *args, **kwargs):
        try:
            request.service = Service.get_from_request(request)
        except ObjectDoesNotExist:
            return HttpResponseRedirect(reverse(create))
        return view(request, *args, **kwargs)
    return func

def service_refused(view):
    def func(request, *args, **kwargs):
        try:
            Service.get_from_request(request)
        except ObjectDoesNotExist:
            return view(request, *args, **kwargs)
        return HttpResponseRedirect(reverse(main))
    return func

def owner_expected(view):
    def func(request, *args, **kwargs):
        if request.service.is_owner(request.user):
            return view(request, *args, **kwargs)
        return render_to_response('multitreehole/not_owner.html', {
        }, context_instance=RequestContext(request))
    return func

def normal_service_expected(view):
    def func(request, *args, **kwargs):
        if request.service.backend:
            return view(request, *args, **kwargs)
        raise Http404
    return func

def login_required(view):
    def func(request, *args, **kwargs):
        if not hasattr(request, 'service') or request.service.backend:
            meta = Service.objects.get(backend__isnull=True)
            decorator = normal_login_required(
                login_url='//' + meta.get_host(request) + settings.LOGIN_URL
            )
        else:
            decorator = normal_login_required
        return decorator(view)(request, *args, **kwargs)
    return func

def get_backend_tuples():
    backends = []
    for backend in get_backends():
        backends.append((backend.slug, backend.label))
    return backends

@service_required
def main(request):
    if request.service.backend:
        request.backend = load_backend(request.service.backend.path)
        return PublishView.as_view()(request)
    else:
        return ListServicesView.as_view()(request)

@service_refused
def create(request):
    if Service.objects.filter(backend__isnull=True).exists():
        if Service.is_creation_allowed(request):
            return render_to_response('multitreehole/create.html', {
                'backends': get_backend_tuples(),
            }, context_instance=RequestContext(request))
        else:
            return HttpResponseForbidden()
    else:
        # Create meta site
        service = Service.new_from_request(request)
        service.save()
        return HttpResponseRedirect(reverse(wait))

class CreateServiceView(View, TemplateResponseMixin):
    template_name = 'multitreehole/create_service.html'
    form_class = ServiceForm

    def dispatch(self, request, backend, *args, **kwargs):
        if not Service.is_creation_allowed(request):
            return HttpResponseForbidden()
        self.backend = get_backend_or_404(backend)
        self.backend_form_class = self.backend.form_class
        return service_refused(login_required(super(CreateServiceView, self).dispatch))(request)

    def get(self, request):
        return self.render_to_response({
            'backend': self.backend,
            'form': self.form_class(),
            'backend_form': self.backend_form_class(prefix='backend'),
        })

    def post(self, request):
        form = self.form_class(request.POST, request.FILES)
        backend_form = self.backend_form_class(request.POST, request.FILES, prefix='backend')
        if form.is_valid() and backend_form.is_valid():
            backend = Backend()
            backend.path = self.backend.__class__.__module__ + '.' + self.backend.__class__.__name__
            backend.params = backend_form.to_json()
            backend.save()
            service = Service.new_from_request(request)
            service.label = form.cleaned_data['label']
            service.params = form.cleaned_data['params']
            service.backend = backend
            # Must request.user.pk; otherwise:
            # long() argument must be a string or a number, not 'SimpleLazyObject'
            service.owners.add(request.user.pk)
            service.save()
            return HttpResponseRedirect(reverse(wait))
        return self.render_to_response({
            'backend': self.backend,
            'form': form,
            'backend_form': backend_form,
        })

def wait(request):
    try:
        Service.get_from_request(request)
    except ObjectDoesNotExist:
        import random
        return HttpResponseRedirect('?_=%f' % random.random())
    return HttpResponseRedirect(reverse(main))

class PublishView(View, TemplateResponseMixin):
    template_name = 'multitreehole/publish.html'
    form_class = PublishForm

    def get(self, request):
        access_level, user_identifier, confirm = request.service.check_access(request)
        if access_level != 'accept':
            # multitreehole/publish-throttle.html
            # multitreehole/publish-reject.html
            return render_to_response('multitreehole/publish-' + access_level + '.html', {
                'user_identifier': user_identifier,
            }, context_instance=RequestContext(request))
        return self.render_to_response({
            'form': self.form_class(),
            'backend_forms': [],
            'access_level': access_level,
            'user_identifier': user_identifier,
        })

    def post(self, request):
        form = self.form_class(request.POST, request.FILES)
        backend_forms = []
        if form.is_valid():
            text = form.cleaned_data['text']
            access_level, user_identifier, confirm = request.service.check_access(request, text)
            def prepare_message():
                message = Message()
                message.set_service(request.service)
                message.user_identifier = user_identifier
                message.text = text
                return message
            if access_level == 'moderate':
                message = prepare_message()
                message.closed = False
                message.save()
                return render_to_response('multitreehole/publish-moderate.html', {
                    'user_identifier': user_identifier,
                    'message': message,
                }, context_instance=RequestContext(request))
            elif access_level == 'accept':
                message = prepare_message()
                message.closed = True
                message.save()
                if confirm(message):
                    client = request.backend.make_client(
                        request.service.backend.pk, request.service.backend.params
                    )
                    backend_message = client.make_message(form.cleaned_data['text'])
                    status = backend_message.publish(request.POST, request.FILES)
                else:
                    status = {'error': ErrorList([_(
                        'Access confirmation failed. Are you requesting concurrently?'
                    )])}
                if 'forms' in status:
                    backend_forms = status['forms']
                if 'error' in status:
                    form._errors['text'] = status['error']
                if 'data' in status:
                    message.backend = request.service.backend
                    message.backend_data = status['data']
                    message.save()
                    return render_to_response('multitreehole/publish-accept.html', {
                        'user_identifier': user_identifier,
                        'message': message,
                    }, context_instance=RequestContext(request))
                message.delete()
        else:
            access_level, user_identifier, confirm = request.service.check_access(request)
        return self.render_to_response({
            'form': form,
            'backend_forms': backend_forms,
            'access_level': access_level,
            'user_identifier': user_identifier,
        })

class ListServicesView(ListView):
    template_name = 'multitreehole/list_services.html'
    context_object_name = 'services'

    def get_queryset(self):
        return Service.objects.exclude(backend__isnull=True)

def go_service(request, slug):
    if Service.validate_slug(slug):
        return HttpResponseRedirect('//' + Service.build_host(slug, request))
    raise Http404

def go(request):
    if 'service' in request.GET:
        return go_service(request, request.GET['service'])
    raise Http404

class ConfigView(View, TemplateResponseMixin):
    template_name = 'multitreehole/config.html'
    form_class = ServiceForm

    @method_decorator(service_required)
    @method_decorator(login_required)
    @method_decorator(owner_expected)
    def dispatch(self, request, *args, **kwargs):
        return super(ConfigView, self).dispatch(request, *args, **kwargs)

    def get(self, request):
        form = self.form_class(initial={
            'label': request.service.label,
            'params': request.service.params,
        })
        return self.render_to_response_with_backends({
            'form': form,
        })

    def post(self, request):
        form = self.form_class(request.POST, request.FILES)
        if form.is_valid():
            request.service.label = form.cleaned_data['label']
            request.service.params = form.cleaned_data['params']
            request.service.save()
            return HttpResponseRedirect('?saved=true')
        return self.render_to_response_with_backends({
            'form': form,
        })

    def render_to_response_with_backends(self, context):
        if self.request.service.backend:
            backend = load_backend(self.request.service.backend.path)
        else:
            backend = None
        context['backend'] = backend
        context['backends'] = get_backend_tuples()
        return self.render_to_response(context)

class ConfigBackendView(View, TemplateResponseMixin):
    template_name = 'multitreehole/config_backend.html'

    def dispatch(self, request, backend, *args, **kwargs):
        self.backend = get_backend_or_404(backend)
        self.form_class = self.backend.form_class
        return service_required(normal_service_expected(login_required(owner_expected(
            super(ConfigBackendView, self).dispatch
        ))))(request)

    def get(self, request):
        return self.render_to_response({
            'backend': self.backend,
            'form': self.form_class(),
        })

    def post(self, request):
        form = self.form_class(request.POST, request.FILES)
        if form.is_valid():
            backend = Backend()
            backend.path = self.backend.__class__.__module__ + '.' + self.backend.__class__.__name__
            backend.params = form.to_json()
            backend.save()
            request.service.backend = backend
            request.service.save()
            return HttpResponseRedirect(reverse('multitreehole.views.config'))
        return self.render_to_response({
            'backend': self.backend,
            'form': form,
        })

class MessageListView(View, TemplateResponseMixin):
    template_name = 'multitreehole/message_list.html'

    @method_decorator(service_required)
    @method_decorator(login_required)
    @method_decorator(owner_expected)
    def dispatch(self, request, *args, **kwargs):
        return super(MessageListView, self).dispatch(request, *args, **kwargs)

    def get(self, request):
        if request.service.backend:
            queryset = Message.filter_service(request.service)
            is_meta = False
        else:
            queryset = Message.objects.all()
            is_meta = True
        f = MessageFilter(request.GET, queryset=queryset)
        try:
            page_size = int(request.GET.get('page_size'))
        except (TypeError, ValueError):
            page_size = getattr(settings, 'MULTITREEHOLE_MESSAGE_PAGE_SIZE', 25)
        paginator = Paginator(f, page_size)
        page = request.GET.get('page')
        try:
            messages = paginator.page(page)
        except PageNotAnInteger:
            messages = paginator.page(1)
        except EmptyPage:
            messages = paginator.page(paginator.num_pages)
        query = request.GET
        if 'page' in query:
            query = query.copy()
            del query['page']
        return self.render_to_response({
            'filter': f,
            'message_list': messages,
            'query_string_piece': '?' + query.urlencode() + '&' if query else '?',
            'page_size': page_size,
            'is_meta': is_meta,
        })

    def post(self, request):
        if not request.service.backend:
            return self.get(request)
        message_ids_to_approve_str = request.POST.getlist('message_approve')
        message_ids_to_reject_str = request.POST.getlist('message_reject')
        if 'batch_approve' in request.POST:
            message_ids_to_approve_str += request.POST.getlist('message')
        if 'batch_reject' in request.POST:
            message_ids_to_approve_str += request.POST.getlist('message')

        def clean_str_list(str_list):
            long_set = set()
            for item in str_list:
                try:
                    long_set.add(long(item))
                except ValueError:
                    pass
            return long_set
        message_ids_to_approve = clean_str_list(message_ids_to_approve_str)
        message_ids_to_reject = clean_str_list(message_ids_to_reject_str)
        message_ids_to_approve, message_ids_to_reject = \
                message_ids_to_approve - message_ids_to_reject, \
                message_ids_to_reject - message_ids_to_approve

        message_ids_approved = set()
        message_ids_rejected = set()
        message_ids_not_approved = set()
        message_ids_not_rejected = set()
        message_objects = {}

        def toggle_message(message_id, closed, approved):
            try:
                message = Message.from_service_id(request.service, message_id)
            except ObjectDoesNotExist:
                message_objects[message_id] = None
                return
            message_objects[message_id] = message
            if message.closed == closed:
                return
            message.closed = closed
            message.approved = approved
            message.save()
            return message

        try:
            from google.appengine.ext import db
            from multitreehole.models import use_ancestor
        except ImportError:
            use_transaction = False
        else:
            use_transaction = use_ancestor

        if use_transaction:
            run_in_transaction = db.run_in_transaction
        else:
            run_in_transaction = lambda func, *args, **kwargs: func(*args, **kwargs)

        client = None
        approve_forms = {}
        approve_errors = {}
        for message_id in message_ids_to_approve:
            try:
                message = run_in_transaction(toggle_message, message_id, True, True)
            except Exception:
                logging.warning('Transaction for approval failure: ' + traceback.format_exc())
                message_ids_not_approved.add(message_id)
                continue
            if not message:
                message_ids_not_approved.add(message_id)
                continue
            if not client:
                client = load_backend(request.service.backend.path).make_client(
                    request.service.backend.pk, request.service.backend.params
                )
            backend_message = client.make_message(message.text)
            status = backend_message.publish(request.POST, request.FILES,
                form_prefix='message_%d' % message_id
            )
            if 'forms' in status:
                approve_forms[message_id] = status['forms']
                toggle_message(message_id, False, None)
            if 'error' in status:
                approve_errors[message_id] = status['error']
                toggle_message(message_id, False, None)
            if 'data' in status:
                message.approved = True
                message.backend = request.service.backend
                message.backend_data = status['data']
                message.save()
                message_ids_approved.add(message_id)
            else:
                message_ids_not_approved.add(message_id)

        for message_id in message_ids_to_reject:
            try:
                message = run_in_transaction(toggle_message, message_id, True, False)
            except Exception:
                logging.warning('Transaction for rejection failure: ' + traceback.format_exc())
                message_ids_not_rejected.add(message_id)
                continue
            if message:
                message_ids_rejected.add(message_id)
            else:
                message_ids_not_rejected.add(message_id)

        # if not message_ids_not_approved and not message_ids_not_rejected \
        #         and not approve_forms and not approve_errors:
        #     return HttpResponseRedirect('?success=true')

        messages = {}
        def populate_set(suffix, local_vars):
            for message_id in local_vars.get('message_ids_' + suffix):
                messages.setdefault(message_id, {})[suffix] = True
        populate_set('to_approve', locals())
        populate_set('to_reject', locals())
        populate_set('approved', locals())
        populate_set('rejected', locals())
        populate_set('not_approved', locals())
        populate_set('not_rejected', locals())
        def populate_dict(obj, key):
            for message_id, sub_obj in obj.iteritems():
                messages.setdefault(message_id, {})[key] = sub_obj
        populate_dict(approve_forms, 'approve_forms')
        populate_dict(approve_errors, 'approve_error')
        populate_dict(message_objects, 'object')
        self.template_name = 'multitreehole/message_action.html'
        return self.render_to_response({
            'action_messages': messages,
            'approve_forms': approve_forms,
        })

@service_required
@normal_service_expected
def message_details(request, message_id):
    try:
        message = Message.from_service_id(request.service, message_id)
    except (ObjectDoesNotExist, TypeError, ValueError):
        raise Http404
    is_owner = request.service.is_owner(request.user)
    return render_to_response('multitreehole/message_details.html', {
        'message': message,
        'is_owner': is_owner,
    }, context_instance=RequestContext(request))
