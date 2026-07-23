from typing import List

from ninja import Router
from ninja.pagination import paginate

from authentication.auth import JWTAuth
from authentication.models import User
from authentication.permissions import require_roles
from companies import services
from companies.schemas import (
    ClientGarmentPriceOut,
    ClientIn,
    ClientOut,
    ClientPriceSetIn,
    CompanyIn,
    CompanyOut,
    LogoConfirmIn,
    LogoUploadOut,
    LogoUploadRequestIn,
    PriceMatrixOut,
)

router = Router(auth=JWTAuth())
clients_router = Router(auth=JWTAuth())


# --- Clientes ---------------------------------------------------------------

@clients_router.get('/', response=List[ClientOut])
@paginate
def list_clients(request, search: str | None = None, is_active: bool | None = None):
    return services.list_clients(search=search, is_active=is_active)


# Ruta literal declarada ANTES de '/{client_id}': Ninja resuelve por forma de URL,
# así que si fuera después, 'price-matrix' intentaría calzar como client_id.
@clients_router.get('/price-matrix', response=PriceMatrixOut)
def price_matrix(request):
    """Matriz comparativa de precios: todas las prendas × todos los clientes."""
    return services.get_price_matrix()


@clients_router.get('/{client_id}', response=ClientOut)
def get_client(request, client_id: int):
    return services.get_client(client_id)


@clients_router.post('/', response={201: ClientOut})
@require_roles(User.Role.SUPERVISOR)
def create_client(request, payload: ClientIn):
    return 201, services.create_client(payload)


@clients_router.put('/{client_id}', response=ClientOut)
@require_roles(User.Role.SUPERVISOR)
def update_client(request, client_id: int, payload: ClientIn):
    return services.update_client(client_id, payload)


@clients_router.delete('/{client_id}', response={204: None})
@require_roles(User.Role.SUPERVISOR)
def deactivate_client(request, client_id: int):
    services.deactivate_client(client_id)
    return 204, None


# --- Catálogo de precios por cliente ---------------------------------------

@clients_router.get('/{client_id}/prices', response=List[ClientGarmentPriceOut])
def list_client_prices(request, client_id: int):
    return services.list_client_prices(client_id)


@clients_router.put('/{client_id}/prices', response=List[ClientGarmentPriceOut])
@require_roles(User.Role.SUPERVISOR)
def set_client_prices(request, client_id: int, payload: ClientPriceSetIn):
    return services.set_client_prices(client_id, payload.prices)


# --- Empresas ---------------------------------------------------------------

@router.get('/', response=List[CompanyOut])
@paginate
def list_companies(request, search: str | None = None, is_active: bool | None = None, client_id: int | None = None):
    # Paginado obligatorio: sin esto el listado trae la tabla completa de
    # empresas en cada carga.
    return services.list_companies(search=search, is_active=is_active, client_id=client_id)


@router.get('/{company_id}', response=CompanyOut)
def get_company(request, company_id: int):
    return services.get_company(company_id)


@router.post('/', response={201: CompanyOut})
@require_roles(User.Role.SUPERVISOR)
def create_company(request, payload: CompanyIn):
    return 201, services.create_company(payload)


@router.put('/{company_id}', response=CompanyOut)
@require_roles(User.Role.SUPERVISOR)
def update_company(request, company_id: int, payload: CompanyIn):
    return services.update_company(company_id, payload)


@router.delete('/{company_id}', response={204: None})
@require_roles(User.Role.SUPERVISOR)
def deactivate_company(request, company_id: int):
    services.deactivate_company(company_id)
    return 204, None


@router.post('/{company_id}/logo-upload-url', response=LogoUploadOut)
def request_logo_upload(request, company_id: int, payload: LogoUploadRequestIn):
    return services.request_logo_upload(company_id, payload.filename, payload.content_type)


@router.post('/{company_id}/logo-confirm', response=CompanyOut)
def confirm_logo_upload(request, company_id: int, payload: LogoConfirmIn):
    return services.confirm_logo_upload(company_id, payload.object_key)
