from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.http import Http404
from django.utils.importlib import import_module

# from django.contrib.auth
def load_backend(path):
    i = path.rfind('.')
    module, attr = path[:i], path[i + 1:]
    try:
        mod = import_module(module)
    except ImportError as e:
        raise ImproperlyConfigured('Error importing tree hole backend %s: "%s"' % (path, e))
    except ValueError:
        raise ImproperlyConfigured('Error importing tree hole backends. Is MULTITREEHOLE_BACKENDS a correctly defined list or tuple?')
    try:
        cls = getattr(mod, attr)
    except AttributeError:
        raise ImproperlyConfigured('Module "%s" does not define a "%s" tree hole backend' % (module, attr))
    return cls()

def get_backends():
    backends = []
    for backend_path in getattr(settings, 'MULTITREEHOLE_BACKENDS', ()):
        backends.append(load_backend(backend_path))
    return backends

def get_backend_or_404(slug):
    for backend_path in getattr(settings, 'MULTITREEHOLE_BACKENDS', ()):
        backend_class = load_backend(backend_path)
        if backend_class.slug == slug:
            return backend_class
    raise Http404
