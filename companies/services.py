from decimal import Decimal

from django.db import transaction
from django.db.models import Count, QuerySet

from companies.models import Client, ClientGarmentPrice, Company
from companies.schemas import ClientIn, ClientPriceIn, CompanyIn
from common.services import build_presigned_upload
from garments.models import GarmentType


# --- Clientes ---------------------------------------------------------------

def list_clients(search: str | None = None, is_active: bool | None = None) -> QuerySet[Client]:
    queryset = Client.objects.annotate(company_count=Count('companies'))
    if search:
        queryset = queryset.filter(name__icontains=search)
    if is_active is not None:
        queryset = queryset.filter(is_active=is_active)
    # Orden determinístico: LIMIT/OFFSET sin order_by no garantiza páginas
    # estables entre requests.
    return queryset.order_by('name')


def get_client(client_id: int) -> Client:
    return Client.objects.annotate(company_count=Count('companies')).get(pk=client_id)


def create_client(payload: ClientIn) -> Client:
    client = Client.objects.create(**payload.dict())
    # El ClientOut necesita company_count; recién creado no tiene empresas.
    client.company_count = 0
    return client


def update_client(client_id: int, payload: ClientIn) -> Client:
    client = Client.objects.get(pk=client_id)
    for field, value in payload.dict().items():
        setattr(client, field, value)
    client.save()
    return get_client(client_id)


def deactivate_client(client_id: int) -> Client:
    client = Client.objects.get(pk=client_id)
    client.is_active = False
    client.save(update_fields=['is_active', 'updated_at'])
    return get_client(client_id)


# --- Catálogo de precios por cliente ---------------------------------------

def list_client_prices(client_id: int) -> QuerySet[ClientGarmentPrice]:
    get_client(client_id)  # valida existencia
    return (
        ClientGarmentPrice.objects.filter(client_id=client_id)
        .select_related('garment_type')
        .order_by('garment_type__code')
    )


@transaction.atomic
def set_client_prices(client_id: int, prices: list[ClientPriceIn]) -> QuerySet[ClientGarmentPrice]:
    """Upsert del catálogo de un cliente: fija/actualiza el precio de cada prenda indicada."""
    get_client(client_id)  # valida existencia
    for price in prices:
        ClientGarmentPrice.objects.update_or_create(
            client_id=client_id,
            garment_type_id=price.garment_type_id,
            defaults={'unit_price': Decimal(str(price.unit_price))},
        )
    return list_client_prices(client_id)


def get_price_matrix() -> dict:
    """Matriz comparativa: cada prenda (fila) con su precio en cada cliente (columna).

    Solo prendas y clientes activos. Tres consultas planas (clientes, prendas,
    precios) que se ensamblan en memoria — nunca un producto cartesiano en la BD.
    Celda `None` = ese cliente no tiene precio definido para esa prenda.
    """
    clients = list(Client.objects.filter(is_active=True).order_by('name').values('id', 'name'))
    garments = list(
        GarmentType.objects.filter(is_active=True).order_by('name').values('id', 'code', 'name')
    )
    price_map = {
        (row['garment_type_id'], row['client_id']): float(row['unit_price'])
        for row in ClientGarmentPrice.objects.filter(is_active=True).values(
            'garment_type_id', 'client_id', 'unit_price'
        )
    }
    client_ids = [c['id'] for c in clients]
    rows = [
        {
            'garment_type_id': g['id'],
            'code': g['code'],
            'name': g['name'],
            'prices': [
                {'client_id': cid, 'unit_price': price_map.get((g['id'], cid))}
                for cid in client_ids
            ],
        }
        for g in garments
    ]
    return {'clients': clients, 'rows': rows}


def get_client_price_map(client_id: int) -> dict[int, Decimal]:
    """Precio por `garment_type_id` del catálogo activo de un cliente.

    Lo usa el cobro (`orders.services.compute_billed_amount`) para congelar el
    monto de la guía leyendo el catálogo vigente del cliente.
    """
    return {
        row['garment_type_id']: row['unit_price']
        for row in ClientGarmentPrice.objects.filter(client_id=client_id, is_active=True).values(
            'garment_type_id', 'unit_price'
        )
    }


# --- Empresas ---------------------------------------------------------------

def list_companies(
    search: str | None = None, is_active: bool | None = None, client_id: int | None = None
) -> QuerySet[Company]:
    queryset = Company.objects.select_related('client')
    if search:
        queryset = queryset.filter(name__icontains=search)
    if is_active is not None:
        queryset = queryset.filter(is_active=is_active)
    if client_id is not None:
        queryset = queryset.filter(client_id=client_id)
    # Orden determinístico: LIMIT/OFFSET sin order_by no garantiza páginas
    # estables entre requests.
    return queryset.order_by('name')


def get_company(company_id: int) -> Company:
    return Company.objects.select_related('client').get(pk=company_id)


@transaction.atomic
def create_company(payload: CompanyIn) -> Company:
    """Crea una empresa. Si no se indica cliente, crea uno 1:1 (caso cliente=empresa).

    Así toda empresa queda siempre bajo un cliente y el resto del sistema
    (catálogo, cobro, reportería) no necesita un caso especial.
    """
    data = payload.dict()
    client_id = data.pop('client_id', None)
    if client_id is None:
        client = Client.objects.create(name=data['name'], is_single_company=True)
        client_id = client.pk
    company = Company.objects.create(client_id=client_id, **data)
    return get_company(company.pk)


@transaction.atomic
def update_company(company_id: int, payload: CompanyIn) -> Company:
    company = Company.objects.select_related('client').get(pk=company_id)
    data = payload.dict()
    client_id = data.pop('client_id', None)
    if client_id is not None and client_id != company.client_id:
        company.client_id = client_id
        # Al mover una empresa a un cliente compartido este deja de ser 1:1.
        Client.objects.filter(pk=client_id, is_single_company=True).update(is_single_company=False)
    for field, value in data.items():
        setattr(company, field, value)
    company.save()
    return get_company(company_id)


def deactivate_company(company_id: int) -> Company:
    company = get_company(company_id)
    company.is_active = False
    company.save(update_fields=['is_active', 'updated_at'])
    return company


def request_logo_upload(company_id: int, filename: str, content_type: str) -> dict:
    get_company(company_id)  # valida existencia
    return build_presigned_upload(folder='empresas/logos', filename=filename, content_type=content_type)


def confirm_logo_upload(company_id: int, object_key: str) -> Company:
    company = get_company(company_id)
    company.logo_key = object_key
    company.save(update_fields=['logo_key', 'updated_at'])
    return company
