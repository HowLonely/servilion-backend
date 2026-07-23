from django.db.models import QuerySet

from garments.models import GarmentType
from garments.schemas import GarmentTypeIn


def list_garment_types(is_active: bool | None = None) -> QuerySet[GarmentType]:
    queryset = GarmentType.objects.all()
    if is_active is not None:
        queryset = queryset.filter(is_active=is_active)
    # Por código, no por nombre: es la referencia que usa el staff a diario
    # (etiquetas, pistoleo, digitalización), así que el catálogo y el selector
    # de prendas quedan navegables por código. También da un orden
    # determinístico para que LIMIT/OFFSET no cambie de página entre requests.
    return queryset.order_by('code')


def get_garment_type(garment_type_id: int) -> GarmentType:
    return GarmentType.objects.get(pk=garment_type_id)


def create_garment_type(payload: GarmentTypeIn) -> GarmentType:
    return GarmentType.objects.create(**payload.dict())


def update_garment_type(garment_type_id: int, payload: GarmentTypeIn) -> GarmentType:
    garment_type = get_garment_type(garment_type_id)
    for field, value in payload.dict().items():
        setattr(garment_type, field, value)
    garment_type.save()
    return garment_type
