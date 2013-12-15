from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist
from django.core.urlresolvers import reverse
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import render_to_response
from django.template import RequestContext
from django.utils.decorators import method_decorator
from django.views.generic import ListView
from django.views.generic.base import View, TemplateResponseMixin

from multitreehole.forms import ServiceForm, PublishForm
from multitreehole.models import Backend, Service, Message
from multitreehole.utils import load_backend, get_backends, get_backend_or_404

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
    @service_required
    @login_required
    def func(request, *args, **kwargs):
        # Must be request.user.pk
        if request.user.pk in request.service.owners or request.user.is_superuser:
            return view(request, *args, **kwargs)
        return render_to_response('multitreehole/not_owner.html', {
        }, context_instance=RequestContext(request))
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
        return render_to_response('multitreehole/create.html', {
            'backends': get_backend_tuples(),
        }, context_instance=RequestContext(request))
    else:
        # Create meta site
        service = Service.new_from_request(request)
        service.save()
        return HttpResponseRedirect(reverse(wait))

class CreateServiceView(View, TemplateResponseMixin):
    template_name = 'multitreehole/create_service.html'
    form_class = ServiceForm

    def dispatch(self, request, backend, *args, **kwargs):
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
        access_level, user_identifier = request.service.check_access(request)
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
            access_level, user_identifier = request.service.check_access(request, text)
            def prepare_message():
                message = Message(service=request.service)
                message.user_identifier = user_identifier
                message.text = text
                return message
            if access_level == 'moderate':
                message = prepare_message()
                message.published = False
                message.save()
                return render_to_response('multitreehole/publish-moderate.html', {
                    'user_identifier': user_identifier,
                    'message': message,
                }, context_instance=RequestContext(request))
            elif access_level == 'accept':
                client = request.backend.make_client(
                    request.service.backend.pk, request.service.backend.params
                )
                backend_message = client.make_message(form.cleaned_data['text'])
                status = backend_message.publish(request.POST, request.FILES)
                if 'forms' in status:
                    backend_forms = status['forms']
                if 'error' in status:
                    form._errors['text'] = status['error']
                if 'data' in status:
                    message = prepare_message()
                    message.published = True
                    message.backend = request.service.backend
                    message.backend_data = status['data']
                    message.save()
                    return render_to_response('multitreehole/publish-accept.html', {
                        'user_identifier': user_identifier,
                        'message': message,
                    }, context_instance=RequestContext(request))
        else:
            access_level, user_identifier = request.service.check_access(request)
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
    if Service.SLUG_RE.match(slug):
        return HttpResponseRedirect('//' + slug + Service.split_request_host(request)[1])
    raise Http404

def go(request):
    if 'service' in request.GET:
        return go_service(request, request.GET['service'])
    raise Http404

class ConfigView(View, TemplateResponseMixin):
    template_name = 'multitreehole/config.html'
    form_class = ServiceForm

    @method_decorator(owner_expected)
    def get(self, request):
        form = self.form_class(initial={
            'label': request.service.label,
            'params': request.service.params,
        })
        return self.render_to_response_with_backends({
            'form': form,
        })

    @method_decorator(owner_expected)
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
        backend = load_backend(self.request.service.backend.path)
        context['backend'] = backend
        context['backends'] = get_backend_tuples()
        return self.render_to_response(context)

class ConfigBackendView(View, TemplateResponseMixin):
    template_name = 'multitreehole/config_backend.html'

    def dispatch(self, request, backend, *args, **kwargs):
        self.backend = get_backend_or_404(backend)
        self.form_class = self.backend.form_class
        return owner_expected(super(ConfigBackendView, self).dispatch)(request)

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
