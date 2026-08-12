from ninja import Schema

from common.schemas import PresignedUploadOut
from common.services import build_object_url


# --- Clientes ---------------------------------------------------------------

class ClientIn(Schema):
    name: str
    tax_id: str = ''
    # Inicial que antecede al correlativo del ref (la "P" de P1375A).
    reference_prefix: str = ''
    contact_name: str = ''
    phone: str = ''


class ClientOut(Schema):
    id: int
    name: str
    tax_id: str
    reference_prefix: str
    contact_name: str
    phone: str
    is_single_company: bool
    is_active: bool
    company_count: int

    @staticmethod
    def resolve_company_count(obj) -> int:
        # `company_count` viene anotado desde el service (list) o se cuenta al vuelo (get).
        return getattr(obj, 'company_count', None) if getattr(obj, 'company_count', None) is not None else obj.companies.count()


# --- Catálogo de precios por cliente ---------------------------------------

class ClientGarmentPriceOut(Schema):
    garment_type_id: int
    code: str
    name: str
    unit_price: float
    is_active: bool

    @staticmethod
    def resolve_code(obj) -> str:
        return obj.garment_type.code

    @staticmethod
    def resolve_name(obj) -> str:
        return obj.garment_type.name


class ClientPriceIn(Schema):
    garment_type_id: int
    unit_price: float


class ClientPriceSetIn(Schema):
    """Upsert del catálogo de un cliente: fija el precio de una o varias prendas."""

    prices: list[ClientPriceIn]


# --- Matriz comparativa de precios (prendas × clientes) --------------------

class PriceMatrixClientOut(Schema):
    """Una columna de la matriz: un cliente."""

    id: int
    name: str


class PriceMatrixCellOut(Schema):
    client_id: int
    # None = el cliente no tiene precio definido para esa prenda (celda vacía).
    unit_price: float | None


class PriceMatrixRowOut(Schema):
    """Una fila de la matriz: una prenda con su precio en cada cliente."""

    garment_type_id: int
    code: str
    name: str
    prices: list[PriceMatrixCellOut]


class PriceMatrixOut(Schema):
    clients: list[PriceMatrixClientOut]
    rows: list[PriceMatrixRowOut]


# --- Empresas ---------------------------------------------------------------

class CompanyIn(Schema):
    name: str
    # Opcional: si no llega, el service crea un cliente 1:1 con el mismo nombre
    # (caso "el cliente es la misma empresa").
    client_id: int | None = None
    tax_id: str = ''
    billing_type: str = 'PRENDAS'
    # PERSONAL (ropa de trabajador) u HOTELERIA (lencería a granel del
    # campamento): decide si la empresa opera con guías o con lotes.
    service_type: str = 'PERSONAL'
    delivery_flow: str = 'FLUJO_1'
    contact_name: str = ''
    phone: str = ''


class CompanyOut(Schema):
    id: int
    client_id: int
    client_name: str
    name: str
    tax_id: str
    billing_type: str
    service_type: str
    delivery_flow: str
    contact_name: str
    phone: str
    logo_url: str | None
    is_active: bool

    @staticmethod
    def resolve_client_name(obj) -> str:
        return obj.client.name

    @staticmethod
    def resolve_logo_url(obj) -> str | None:
        return build_object_url(obj.logo_key)


class LogoUploadRequestIn(Schema):
    filename: str
    content_type: str


class LogoUploadOut(PresignedUploadOut):
    pass


class LogoConfirmIn(Schema):
    object_key: str
