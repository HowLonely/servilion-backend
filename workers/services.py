from django.db.models import Q, QuerySet

from workers.models import Worker
from workers.schemas import WorkerIn


def list_workers(
    company_id: int | None = None,
    search: str | None = None,
    is_active: bool | None = None,
) -> QuerySet[Worker]:
    queryset = Worker.objects.select_related('company').all()
    if company_id is not None:
        queryset = queryset.filter(company_id=company_id)
    if search:
        queryset = queryset.filter(
            Q(full_name__icontains=search) | Q(national_id__icontains=search) | Q(badge_code__icontains=search)
        )
    if is_active is not None:
        queryset = queryset.filter(is_active=is_active)
    # Orden determinístico: LIMIT/OFFSET sin order_by no garantiza páginas
    # estables entre requests.
    return queryset.order_by('full_name')


def get_worker(worker_id: int) -> Worker:
    return Worker.objects.select_related('company').get(pk=worker_id)


def create_worker(payload: WorkerIn) -> Worker:
    return Worker.objects.create(**payload.dict())


def update_worker(worker_id: int, payload: WorkerIn) -> Worker:
    worker = get_worker(worker_id)
    for field, value in payload.dict().items():
        setattr(worker, field, value)
    worker.save()
    return worker


def deactivate_worker(worker_id: int) -> Worker:
    worker = get_worker(worker_id)
    worker.is_active = False
    worker.save(update_fields=['is_active', 'updated_at'])
    return worker
