from datetime import datetime
from uuid import UUID

from ninja import Schema

from common.schemas import PresignedUploadOut


class OrderItemIn(Schema):
    """Línea de detalle de la OT.

    `garment_type_id` es opcional porque la OT física admite prendas fuera del
    catálogo preimpreso, escritas a mano por el trabajador; en ese caso llega
    `custom_name`. El precio ya no viaja por línea: se define por cliente en el
    catálogo (`companies.ClientGarmentPrice`) y se congela al cobrar.
    """

    garment_type_id: int | None = None
    custom_name: str = ''
    quantity: int


class OrderItemOut(Schema):
    garment_type_id: int | None
    code: str
    name: str
    quantity: int
    scanned_quantity: int

    @staticmethod
    def resolve_code(obj) -> str:
        # El código del catálogo es la referencia que usan digitadores y
        # lavandería a diario; las prendas fuera de catálogo no tienen uno
        # (ya se distinguen con el badge "Fuera de catálogo" en el frontend).
        return obj.garment_type.code if obj.garment_type_id else ''

    @staticmethod
    def resolve_name(obj) -> str:
        return obj.display_name


class LaundryOrderIn(Schema):
    order_number: str
    worker_id: int
    ticket_number: str = ''
    shift: str = ''
    weight_kg: float | None = None
    received_at: datetime
    laundry_received_at: datetime | None = None
    promised_at: datetime | None = None
    observations: str = ''
    reference: str = ''
    control_code: str = ''
    items: list[OrderItemIn] = []


class CleanReceptionOut(Schema):
    """Una llegada física del morral limpio a faena (repetible: ver `SiteScan`).

    Puede haber más de una por guía cuando el envío original salió incompleto
    y la prenda faltante llega después, en un envío aparte.
    """

    received_at: datetime
    note: str


class MissingItemResolutionOut(Schema):
    item_id: int
    item_code: str
    item_name: str
    resolution_type: str
    quantity: int
    purchase_cost: float | None
    resolved_at: datetime
    resolved_by_name: str | None
    note: str

    @staticmethod
    def resolve_item_code(obj) -> str:
        return obj.item.garment_type.code if obj.item.garment_type_id else ''

    @staticmethod
    def resolve_item_name(obj) -> str:
        return obj.item.display_name

    @staticmethod
    def resolve_resolved_by_name(obj) -> str | None:
        return obj.resolved_by.get_full_name() if obj.resolved_by_id else None


class LaundryOrderOut(Schema):
    id: int
    client_uuid: UUID
    order_number: str | None
    ticket_number: str
    worker_id: int
    worker_name: str
    company_id: int
    company_name: str
    client_id: int
    client_name: str
    delivery_flow: str
    shift: str
    status: str
    garment_count: int
    weight_kg: float | None
    received_at: datetime
    site_received_at: datetime | None
    laundry_received_at: datetime | None
    packed_at: datetime | None
    promised_at: datetime | None
    incomplete_at: datetime | None
    completed_at: datetime | None
    clean_receptions: list[CleanReceptionOut]
    delivered_at: datetime | None
    observations: str
    reference: str
    control_code: str
    photo_url: str | None
    items: list[OrderItemOut]
    missing_item_resolutions: list[MissingItemResolutionOut]
    updated_at: datetime

    @staticmethod
    def resolve_worker_name(obj) -> str:
        return obj.worker.full_name

    @staticmethod
    def resolve_company_name(obj) -> str:
        return obj.company.name

    @staticmethod
    def resolve_client_id(obj) -> int:
        return obj.company.client_id

    @staticmethod
    def resolve_client_name(obj) -> str:
        return obj.company.client.name

    @staticmethod
    def resolve_delivery_flow(obj) -> str:
        return obj.company.delivery_flow

    @staticmethod
    def resolve_photo_url(obj) -> str | None:
        from common.services import build_object_url

        return build_object_url(obj.photo_key)

    @staticmethod
    def resolve_clean_receptions(obj):
        from orders.models import SiteScan

        return [
            {'received_at': scan.scanned_at, 'note': scan.note}
            for scan in obj.site_scans.filter(kind=SiteScan.Kind.CLEAN_IN).order_by('scanned_at')
        ]

    @staticmethod
    def resolve_missing_item_resolutions(obj):
        return obj.missing_item_resolutions.select_related('item', 'item__garment_type', 'resolved_by').all()


class StatusUpdateIn(Schema):
    status: str
    note: str = ''


class OrderStatusHistoryOut(Schema):
    id: int
    previous_status: str
    new_status: str
    changed_at: datetime
    changed_by_name: str | None
    note: str

    @staticmethod
    def resolve_changed_by_name(obj) -> str | None:
        return str(obj.changed_by) if obj.changed_by_id else None


class NoteIn(Schema):
    note: str = ''


class SiteScanIn(Schema):
    """Pistoleo de recepción de ropa sucia en faena (app PEÑON, paso 2)."""

    code: str
    scanned_at: datetime | None = None
    note: str = ''


class SiteScanOut(Schema):
    id: int
    kind: str
    scanned_code: str
    order_id: int | None
    scanned_at: datetime
    note: str
    matched: bool

    @staticmethod
    def resolve_matched(obj) -> bool:
        return obj.order_id is not None


class PackingScanIn(Schema):
    """Pistoleo de una prenda al empacar el morral limpio (paso 6)."""

    code: str
    quantity: int = 1


class PackingItemProgressOut(Schema):
    item_id: int
    garment_type_id: int | None
    code: str
    name: str
    quantity: int
    scanned_quantity: int


class PackingProgressOut(Schema):
    order_id: int
    declared_total: int
    scanned_total: int
    is_complete: bool
    items: list[PackingItemProgressOut]


class ResolveMissingItemIn(Schema):
    """Resuelve una prenda que faltó en una guía INCOMPLETA.

    `code` es obligatorio si `resolution_type` es ENCONTRADA (se valida contra
    el código de la prenda); `purchase_cost` es obligatorio si es COMPRADA.
    """

    item_id: int
    resolution_type: str
    quantity: int = 1
    code: str = ''
    purchase_cost: float | None = None
    note: str = ''


class ReceiptItemOut(Schema):
    name: str
    quantity: int


class ReceiptOut(Schema):
    """Boleta impresa que acompaña la ropa limpia de vuelta a faena."""

    order_number: str | None
    reference: str
    control_code: str
    ticket_number: str
    company_name: str
    worker_name: str
    national_id: str
    camp: str
    room: str
    shift: str
    weight_kg: float | None
    garment_count: int
    promised_at: datetime | None
    qr_payload: str
    items: list[ReceiptItemOut]


class SiteCountersOut(Schema):
    dispatched: int
    delivered: int
    clean_received_at_site: int
    pending_delivery: int
    by_status: dict[str, int]


class PhotoUploadRequestIn(Schema):
    filename: str
    content_type: str


class PhotoUploadOut(PresignedUploadOut):
    pass


class PhotoConfirmIn(Schema):
    object_key: str


class OrderSyncIn(Schema):
    client_uuid: UUID
    order_number: str
    worker_id: int
    ticket_number: str = ''
    shift: str = ''
    status: str = 'RECIBIDA'
    weight_kg: float | None = None
    received_at: datetime
    promised_at: datetime | None = None
    delivered_at: datetime | None = None
    observations: str = ''
    reference: str = ''
    control_code: str = ''
    items: list[OrderItemIn] = []
    updated_at: datetime


class OrderSyncBatchIn(Schema):
    orders: list[OrderSyncIn]


class OrderSyncResultOut(Schema):
    client_uuid: UUID
    server_id: int
    result: str
    updated_at: datetime


class OrderSyncBatchOut(Schema):
    results: list[OrderSyncResultOut]


class SyncConflictOut(Schema):
    id: int
    order_id: int
    order_number: str | None
    worker_name: str
    client_uuid: UUID
    device_updated_at: datetime
    server_updated_at: datetime
    discarded_payload: dict
    created_at: datetime
    resolved_at: datetime | None
    resolution_note: str

    @staticmethod
    def resolve_order_number(obj) -> str | None:
        return obj.order.order_number

    @staticmethod
    def resolve_worker_name(obj) -> str:
        return obj.order.worker.full_name
