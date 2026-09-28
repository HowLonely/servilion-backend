from datetime import datetime
from uuid import UUID

from ninja import Schema
from pydantic import Field


class LinenLineIn(Schema):
    garment_type_id: int
    quantity: int


class DispatchIn(Schema):
    """Despacho de lencería limpia desde la planta a la faena del cliente."""

    company_id: int
    lines: list[LinenLineIn]
    note: str = ''


class CountLineIn(Schema):
    garment_type_id: int
    # Lo contado físicamente. 0 es un conteo válido: "aquí no hay ninguna".
    counted: int


class CountIn(Schema):
    """Conteo de inventario: carga inicial o reajuste de un lugar.

    `camp_id` null es la bodega de faena. Los tipos que no vienen en `lines` no
    se tocan: contar solo las sábanas no pone las toallas en cero.
    """

    company_id: int
    camp_id: int | None = None
    lines: list[CountLineIn]
    note: str = ''


class FieldMovementIn(Schema):
    """Reparto o retiro registrado en faena por la app móvil, con o sin señal."""

    client_uuid: UUID
    kind: str  # "REPARTO" o "RETIRO"
    company_id: int
    camp_id: int
    lines: list[LinenLineIn]
    # Momento del teléfono, no el de la sincronización.
    occurred_at: datetime
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    accuracy_meters: float = Field(gt=0)
    note: str = ''


class FieldMovementBatchIn(Schema):
    movements: list[FieldMovementIn]


class FieldMovementResultOut(Schema):
    """Resultado de un movimiento de la cola del teléfono.

    `DUPLICADO` no es un error: significa que el servidor ya lo tenía (se
    perdió la respuesta de un envío anterior) y la app debe darlo por enviado.
    `ERROR` es un rechazo de negocio; reintentar no lo va a arreglar.
    """

    client_uuid: UUID
    status: str  # CREADO | DUPLICADO | ERROR
    movement_id: int | None = None
    detail: str = ''


class FieldMovementBatchOut(Schema):
    results: list[FieldMovementResultOut]


class VoidMovementIn(Schema):
    reason: str


class LinenLineOut(Schema):
    garment_type_id: int
    code: str
    name: str
    quantity: int
    difference: int | None

    @staticmethod
    def resolve_code(obj) -> str:
        return obj.garment_type.code

    @staticmethod
    def resolve_name(obj) -> str:
        return obj.garment_type.name


class LinenMovementOut(Schema):
    id: int
    client_uuid: UUID
    kind: str
    kind_label: str
    number: str
    company_id: int
    company_name: str
    camp_id: int | None
    camp_name: str
    # Nombre del lugar que el movimiento toca, para listarlo sin pensar en el
    # tipo: "Campamento Central", "Bodega de faena" o "Faena" (despacho).
    location_name: str
    occurred_at: datetime
    registered_by_name: str
    note: str
    total_quantity: int
    lines: list[LinenLineOut]
    is_voided: bool
    voided_at: datetime | None
    voided_by_name: str
    void_reason: str

    @staticmethod
    def resolve_kind_label(obj) -> str:
        return obj.get_kind_display()

    @staticmethod
    def resolve_company_name(obj) -> str:
        return obj.company.name

    @staticmethod
    def resolve_camp_name(obj) -> str:
        return obj.camp.name if obj.camp_id else ''

    @staticmethod
    def resolve_location_name(obj) -> str:
        if obj.camp_id:
            return obj.camp.name
        return 'Bodega de faena' if obj.kind == 'CONTEO' else 'Faena'

    @staticmethod
    def resolve_registered_by_name(obj) -> str:
        user = obj.registered_by
        return (user.get_full_name() or user.username) if user else ''

    @staticmethod
    def resolve_voided_by_name(obj) -> str:
        user = obj.voided_by
        return (user.get_full_name() or user.username) if user else ''

    @staticmethod
    def resolve_total_quantity(obj) -> int:
        return sum(line.quantity for line in obj.lines.all())

    @staticmethod
    def resolve_lines(obj) -> list:
        return list(obj.lines.all())


class LinenTypeOut(Schema):
    id: int
    code: str
    name: str


class BalanceLineOut(Schema):
    garment_type_id: int
    quantity: int


class BalanceLocationOut(Schema):
    """Una fila del saldo: un lugar y cuántas piezas de cada tipo tiene.

    `has_negative` avisa que el sistema cree que hay menos de cero: se retiró
    más de lo que tenía registrado. No es un error del registro en terreno,
    sino una señal de que ese lugar necesita un conteo de inventario.
    """

    kind: str  # SERVILION | BODEGA_FAENA | CAMPAMENTO
    camp_id: int | None
    name: str
    lines: list[BalanceLineOut]
    total: int
    has_negative: bool
    last_counted_at: datetime | None


class CompanyBalanceOut(Schema):
    company_id: int
    company_name: str
    faena_name: str
    linen_types: list[LinenTypeOut]
    locations: list[BalanceLocationOut]


class DispatchPrintLineOut(Schema):
    code: str
    name: str
    quantity: int


class DispatchPrintJobOut(Schema):
    """Datos de la guía de despacho, sin layout: la terminal arma el ticket."""

    number: str
    company_name: str
    faena: str
    occurred_at: datetime
    registered_by_name: str
    note: str
    total_quantity: int
    lines: list[DispatchPrintLineOut]
