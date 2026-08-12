from datetime import datetime

from ninja import Schema


class LinenBatchItemIn(Schema):
    """Una línea de la carga: tipo de lencería y cuánto entró.

    `garment_type_id` es opcional porque el campamento manda lencería que no
    siempre está en el catálogo; en ese caso llega `custom_name`.
    """

    garment_type_id: int | None = None
    custom_name: str = ''
    quantity_in: int
    weight_kg: float | None = None


class LinenBatchIn(Schema):
    company_id: int
    camp_id: int | None = None
    received_at: datetime | None = None
    promised_at: datetime | None = None
    weight_kg: float | None = None
    observations: str = ''
    items: list[LinenBatchItemIn] = []


class LinenBatchItemOut(Schema):
    id: int
    garment_type_id: int | None
    name: str
    quantity_in: int
    quantity_out: int | None
    shortage: int | None
    weight_kg: float | None

    @staticmethod
    def resolve_name(obj) -> str:
        return obj.display_name


class LinenBatchOut(Schema):
    id: int
    batch_number: str
    company_id: int
    company_name: str
    company_logo_url: str | None
    camp_id: int | None
    camp_name: str
    status: str
    received_at: datetime
    promised_at: datetime | None
    dispatched_at: datetime | None
    weight_kg: float | None
    observations: str
    received_by_client: str
    items: list[LinenBatchItemOut]
    # Totales del lote, para no recalcularlos en cada pantalla.
    total_in: int
    total_out: int | None
    shortage: int | None
    is_counted: bool
    updated_at: datetime

    @staticmethod
    def resolve_company_name(obj) -> str:
        return obj.company.name

    @staticmethod
    def resolve_company_logo_url(obj) -> str | None:
        from common.services import build_object_url

        return build_object_url(obj.company.logo_key)

    @staticmethod
    def resolve_camp_name(obj) -> str:
        return obj.camp.name if obj.camp_id else ''

    @staticmethod
    def resolve_total_in(obj) -> int:
        return sum(item.quantity_in for item in obj.items.all())

    @staticmethod
    def resolve_total_out(obj) -> int | None:
        counted = [item for item in obj.items.all() if item.quantity_out is not None]
        return sum(item.quantity_out for item in counted) if counted else None

    @staticmethod
    def resolve_shortage(obj) -> int | None:
        items = list(obj.items.all())
        counted = [item for item in items if item.quantity_out is not None]
        if not items or len(counted) != len(items):
            return None
        return sum(item.quantity_in - item.quantity_out for item in counted)

    @staticmethod
    def resolve_is_counted(obj) -> bool:
        items = list(obj.items.all())
        return bool(items) and all(item.quantity_out is not None for item in items)


class ReturnCountIn(Schema):
    """Cuántas piezas de una línea volvieron del lavado."""

    item_id: int
    quantity_out: int


class ReturnCountBatchIn(Schema):
    counts: list[ReturnCountIn]


class DispatchIn(Schema):
    received_by_client: str = ''
    note: str = ''


class BatchNoteItemOut(Schema):
    item_id: int
    name: str
    quantity_in: int
    quantity_out: int | None
    shortage: int | None


class BatchNoteOut(Schema):
    """Acta de devolución que acompaña la carga limpia de vuelta a faena."""

    batch_number: str
    company_name: str
    company_logo_url: str | None
    camp: str
    status: str
    received_at: datetime
    promised_at: datetime | None
    dispatched_at: datetime | None
    weight_kg: float | None
    received_by_client: str
    observations: str
    items: list[BatchNoteItemOut]
    total_in: int
    total_out: int | None
    shortage: int | None
    is_counted: bool


class HospitalityCountersOut(Schema):
    batches: int
    in_plant: int
    dispatched: int
    weight_kg: float | None
    pieces_in: int
    pieces_out: int
    shortage: int
    shortage_rate: float | None
