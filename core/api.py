import logging

from django.core.exceptions import ObjectDoesNotExist
from django.http import Http404
from ninja import NinjaAPI
from ninja.errors import ValidationError

from authentication.api import router as auth_router
from authentication.permissions import PermissionDenied
from companies.api import clients_router, router as companies_router
from camps.api import camps_router, faenas_router, rooms_router
from garments.api import router as garments_router
from hospitality.api import router as hospitality_router
from orders.api import router as orders_router
from orders.delivery_api import router as delivery_router
from orders.report_api import router as reports_router
from workers.api import router as workers_router

api = NinjaAPI(
    title='Servilion API',
    version='1.0.0',
    description='API del sistema de gestión de lavandería industrial Servilion.',
)


@api.exception_handler(Http404)
@api.exception_handler(ObjectDoesNotExist)
def not_found_handler(request, exc):
    return api.create_response(request, {'detail': 'No encontrado.'}, status=404)


@api.exception_handler(PermissionDenied)
def permission_denied_handler(request, exc):
    # 403 y no 401: el token es válido, lo que falta es el rol.
    return api.create_response(request, {'detail': str(exc)}, status=403)


@api.exception_handler(ValidationError)
def validation_error_handler(request, exc):
    return api.create_response(request, {'detail': 'Datos inválidos.', 'errors': exc.errors}, status=422)


@api.exception_handler(Exception)
def unhandled_exception_handler(request, exc):
    # Red de seguridad para que ningún error deje escapar HTML/traceback:
    # la API-first rule exige responder siempre JSON, incluso ante bugs.
    logging.getLogger('django').exception('Unhandled API error', exc_info=exc)
    return api.create_response(request, {'detail': 'Error interno del servidor.'}, status=500)


api.add_router('/auth/', auth_router, tags=['Autenticación'])
api.add_router('/clients/', clients_router, tags=['Clientes'])
api.add_router('/companies/', companies_router, tags=['Empresas'])
api.add_router('/workers/', workers_router, tags=['Trabajadores'])
api.add_router('/garments/', garments_router, tags=['Prendas'])
api.add_router('/faenas/', faenas_router, tags=['Faenas'])
api.add_router('/camps/', camps_router, tags=['Campamentos'])
api.add_router('/rooms/', rooms_router, tags=['Habitaciones'])
api.add_router('/orders/', orders_router, tags=['Guías'])
api.add_router('/delivery/', delivery_router, tags=['Entrega en habitación'])
api.add_router('/hospitality/', hospitality_router, tags=['Hotelería'])
api.add_router('/reports/', reports_router, tags=['Reportes'])
