from datetime import datetime

from ninja import Schema


class WeighInIn(Schema):
    """Lo que la báscula manda: los cuatro datos que el operador toca en pantalla.

    No lleva `reference` ni fecha: el ref lo emite el servidor (es un correlativo
    que debe serializarse entre estaciones) y el momento del pesaje es el de la
    petición, no uno que el operador pueda elegir.
    """

    client_id: int
    company_id: int
    garment_count: int
    weight_kg: float


class WeighLabelOut(Schema):
    """Un adhesivo. `code` es a la vez lo impreso y lo que lee la pistola."""

    sequence: int
    code: str
    scanned_at: datetime | None


class WeighInOut(Schema):
    id: int
    reference: str
    status: str
    status_label: str

    client_id: int
    client_name: str
    company_id: int
    company_name: str
    # La etiqueta y el ticket imprimen "FAENA · EMPRESA" y suman "CONTRATISTA"
    # cuando corresponde, igual que la etiqueta lavable actual.
    faena: str
    is_contractor: bool

    garment_count: int
    weight_kg: float
    weighed_at: datetime
    weighed_by_name: str

    order_id: int | None
    order_number: str
    digitized_at: datetime | None
    voided_at: datetime | None
    void_reason: str

    labels: list[WeighLabelOut]

    @staticmethod
    def resolve_status_label(obj) -> str:
        return obj.get_status_display()

    @staticmethod
    def resolve_client_name(obj) -> str:
        return obj.client.name

    @staticmethod
    def resolve_company_name(obj) -> str:
        return obj.company.name

    @staticmethod
    def resolve_faena(obj) -> str:
        return obj.client.faena.name if obj.client.faena_id else ''

    @staticmethod
    def resolve_is_contractor(obj) -> bool:
        return obj.company.is_contractor

    @staticmethod
    def resolve_weight_kg(obj) -> float:
        return float(obj.weight_kg)

    @staticmethod
    def resolve_weighed_by_name(obj) -> str:
        return obj.weighed_by.get_full_name() or obj.weighed_by.username if obj.weighed_by_id else ''

    @staticmethod
    def resolve_order_number(obj) -> str:
        return (obj.order.order_number or '') if obj.order_id else ''

    @staticmethod
    def resolve_labels(obj) -> list:
        return list(obj.labels.all())


class VoidWeighInIn(Schema):
    reason: str = ''


class PrintJobOut(Schema):
    """Datos para imprimir, sin layout: el cliente arma el ZPL.

    Misma convención que `orders.services.build_receipt` y `build_garment_labels`:
    el backend no sabe de milímetros ni de modelos de impresora. Aquí la que
    imprime es la terminal de escritorio, que además es la única que conoce el
    puerto donde está colgada la etiquetera.
    """

    reference: str
    client_name: str
    company_name: str
    faena: str
    is_contractor: bool
    garment_count: int
    weight_kg: float
    weighed_at: datetime
    weighed_by_name: str
    # Un elemento por adhesivo de prenda. El ticket maestro va aparte porque
    # lleva otra información y otro tamaño.
    labels: list[WeighLabelOut]
