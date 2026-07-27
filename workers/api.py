from typing import List

from ninja import Router
from ninja.pagination import paginate

from authentication.auth import JWTAuth
from authentication.permissions import require_admin
from workers import services
from workers.schemas import WorkerIn, WorkerOut

router = Router(auth=JWTAuth())


@router.get('/', response=List[WorkerOut])
@paginate
def list_workers(
    request,
    company_id: int | None = None,
    search: str | None = None,
    is_active: bool | None = None,
):
    # Paginado obligatorio: sin esto el listado trae la tabla completa de
    # trabajadores en cada carga.
    return services.list_workers(company_id=company_id, search=search, is_active=is_active)


@router.get('/{worker_id}', response=WorkerOut)
def get_worker(request, worker_id: int):
    return services.get_worker(worker_id)


@router.post('/', response={201: WorkerOut})
@require_admin()
def create_worker(request, payload: WorkerIn):
    return 201, services.create_worker(payload)


@router.put('/{worker_id}', response=WorkerOut)
@require_admin()
def update_worker(request, worker_id: int, payload: WorkerIn):
    return services.update_worker(worker_id, payload)


@router.delete('/{worker_id}', response={204: None})
@require_admin()
def deactivate_worker(request, worker_id: int):
    services.deactivate_worker(worker_id)
    return 204, None
