"""Una función WSGI para todas las rutas de pedidos en Vercel."""
from urllib.parse import parse_qs
from cdl_backend import application


def app(environ, start_response):
    values = parse_qs(environ.get('QUERY_STRING', ''))
    suffix = values.get('__cdl_path', [None])[-1]
    if suffix is not None:
        environ = dict(environ, PATH_INFO='/api/' + suffix)
    return application(environ, start_response)
