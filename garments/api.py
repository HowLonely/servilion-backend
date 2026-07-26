from typing import List

from ninja import Router
from ninja.pagination import paginate

from authentication.auth import JWTAuth
from authentication.permissions import require_admin
from garments import services
from garments.schemas import GarmentTypeIn, GarmentTypeOut

router = Router(auth=JWTAuth())


@router.get('/', response=List[GarmentTypeOut])
@paginate
def list_garment_types(request, is_active: bool | None = None):
    return services.list_garment_types(is_active=is_active)


@router.get('/{garment_type_id}', response=GarmentTypeOut)
def get_garment_type(request, garment_type_id: int):
    return services.get_garment_type(garment_type_id)


@router.post('/', response={201: GarmentTypeOut})
@require_admin()
def create_garment_type(request, payload: GarmentTypeIn):
    return 201, services.create_garment_type(payload)


@router.put('/{garment_type_id}', response=GarmentTypeOut)
@require_admin()
def update_garment_type(request, garment_type_id: int, payload: GarmentTypeIn):
    return services.update_garment_type(garment_type_id, payload)
