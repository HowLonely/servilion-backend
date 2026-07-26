from datetime import datetime
from typing import List

from celery.result import AsyncResult
from ninja import Router
from ninja.pagination import paginate

from authentication.auth import JWTAuth
from authentication.models import User
from authentication.permissions import require_admin, require_roles
from common.schemas import MessageOut
from orders import services
from orders.schemas import (
    BillingReportRequestIn,
    BillingReportResultOut,
    BillingReportTaskOut,
    LaundryOrderIn,
    LaundryOrderOut,
    NoteIn,
    OrderStatusHistoryOut,
    OrderSyncBatchIn,
    OrderSyncBatchOut,
    PackingProgressOut,
    PackingScanIn,
    PhotoConfirmIn,
    PhotoUploadOut,
    PhotoUploadRequestIn,
    ReceiptOut,
    ResolveMissingItemIn,
    SiteCountersOut,
    SiteScanIn,
    SiteScanOut,
    StatusUpdateIn,
    SyncConflictOut,
)
from orders.tasks import generate_billing_report_task

router = Router(auth=JWTAuth())

# Django Ninja resuelve rutas por forma de URL antes de mirar el método HTTP,
# así que un path literal de un segmento (ej. "sync") puede calzar con
# "/{order_id}" si ese patrón se registra antes. Por eso todas las rutas
# literales van declaradas ANTES de las rutas con parámetros de path.


@router.get('/', response=List[LaundryOrderOut])
@paginate
def list_orders(
    request,
    status: str | None = None,
    company_id: int | None = None,
    client_id: int | None = None,
    worker_id: int | None = None,
    search: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
):
    # Paginado obligatorio: el histórico legado ronda las 280.000 guías, así que
    # un listado sin límite no es viable ni siquiera con filtros laxos.
    return services.list_orders(
        status=status,
        company_id=company_id,
        client_id=client_id,
        worker_id=worker_id,
        search=search,
        date_from=date_from,
        date_to=date_to,
    )


@router.post('/', response={201: LaundryOrderOut})
@require_roles(User.Role.DIGITADOR_OT, User.Role.SUPERVISOR)
def create_order(request, payload: LaundryOrderIn):
    """Digitalización de la OT física al llegar el morral a Antofagasta (paso 4)."""
    order = services.create_order(payload, received_by=request.auth)
    return 201, services.get_order(order.id)


@router.post('/scan/site-reception', response={201: SiteScanOut})
@require_roles(User.Role.SUPERVISOR)
def register_site_reception(request, payload: SiteScanIn):
    """Pistoleo del morral sucio en faena (paso 2), previo a la digitalización."""
    scan = services.register_site_reception(
        payload.code, user=request.auth, scanned_at=payload.scanned_at, note=payload.note
    )
    return 201, scan


@router.get('/scan/{code}', response={200: LaundryOrderOut, 404: MessageOut})
def find_order_by_code(request, code: str):
    """Resuelve un código pistoleado (OT, ref o control) a la guía correspondiente."""
    return 200, services.find_order_by_code(code)


@router.get('/counters', response=SiteCountersOut)
def get_counters(request, date_from: datetime | None = None, date_to: datetime | None = None):
    """Contadores ENTREGADOS / DESPACHADOS de la operación en faena."""
    return services.get_site_counters(date_from=date_from, date_to=date_to)


@router.get('/sync-conflicts', response=List[SyncConflictOut])
@require_admin()
@paginate
def list_sync_conflicts(request, resolved: bool | None = None):
    """Cambios de la app móvil descartados por antigüedad, para revisión del administrador."""
    return services.list_sync_conflicts(resolved=resolved)


@router.post('/sync-conflicts/{conflict_id}/resolve', response=SyncConflictOut)
@require_admin()
def resolve_sync_conflict(request, conflict_id: int, payload: NoteIn):
    return services.resolve_sync_conflict(conflict_id, user=request.auth, note=payload.note)


@router.post('/sync', response=OrderSyncBatchOut)
def sync_orders(request, payload: OrderSyncBatchIn):
    """Sincronización masiva en lote desde la app móvil offline-first."""
    results = services.sync_batch(payload.orders)
    return {
        'results': [
            {
                'client_uuid': order.client_uuid,
                'server_id': order.id,
                'result': result,
                'updated_at': order.updated_at,
            }
            for order, result in results
        ]
    }


@router.post('/reports/billing', response=BillingReportTaskOut)
@require_admin()
def request_billing_report(request, payload: BillingReportRequestIn):
    task = generate_billing_report_task.delay(
        payload.date_from.isoformat(),
        payload.date_to.isoformat(),
        company_id=payload.company_id,
        client_id=payload.client_id,
    )
    return {'task_id': task.id}


@router.get('/reports/billing/{task_id}', response=BillingReportResultOut)
@require_admin()
def get_billing_report(request, task_id: str):
    result = AsyncResult(task_id)
    return {'status': result.status, 'result': result.result if result.ready() and result.successful() else None}


@router.get('/{order_id}', response=LaundryOrderOut)
def get_order(request, order_id: int):
    return services.get_order(order_id)


@router.get('/{order_id}/history', response=List[OrderStatusHistoryOut])
def get_status_history(request, order_id: int):
    return services.get_status_history(order_id)


@router.get('/{order_id}/receipt', response=ReceiptOut)
def get_receipt(request, order_id: int):
    """Datos de la boleta impresa que acompaña el morral limpio de vuelta a faena."""
    return services.build_receipt(order_id)


@router.patch('/{order_id}/status', response={200: LaundryOrderOut, 400: MessageOut})
def update_status(request, order_id: int, payload: StatusUpdateIn):
    try:
        order = services.update_status(order_id, payload.status, user=request.auth, note=payload.note)
    except services.InvalidStatusTransition as exc:
        return 400, {'detail': str(exc)}
    return 200, services.get_order(order.id)


@router.get('/{order_id}/packing', response=PackingProgressOut)
def get_packing_progress(request, order_id: int):
    return services.get_packing_progress(order_id)


@router.post('/{order_id}/packing/scan', response={200: PackingProgressOut, 400: MessageOut})
@require_roles(User.Role.DIGITADOR_EMPAQUE, User.Role.SUPERVISOR)
def scan_packed_garment(request, order_id: int, payload: PackingScanIn):
    """Suma una prenda pistoleada al morral limpio durante el empaque (paso 6)."""
    try:
        return 200, services.scan_packed_garment(order_id, payload.code, payload.quantity, user=request.auth)
    except services.OrderFlowError as exc:
        return 400, {'detail': str(exc)}


@router.post('/{order_id}/packing/finish', response={200: LaundryOrderOut, 400: MessageOut})
@require_roles(User.Role.DIGITADOR_EMPAQUE, User.Role.SUPERVISOR)
def finish_packing(request, order_id: int, payload: NoteIn):
    """Cierra el empaque: completa la guía o la marca incompleta según el pistoleo."""
    try:
        services.finish_packing(order_id, user=request.auth, note=payload.note)
    except services.InvalidStatusTransition as exc:
        return 400, {'detail': str(exc)}
    return 200, services.get_order(order_id)


@router.post('/{order_id}/incomplete/resolve', response={200: LaundryOrderOut, 400: MessageOut})
@require_roles(User.Role.DIGITADOR_EMPAQUE, User.Role.SUPERVISOR)
def resolve_missing_item(request, order_id: int, payload: ResolveMissingItemIn):
    """Resuelve una prenda faltante de una guía Incompleta: encontrada (pistoleo) o comprada (costo)."""
    try:
        return 200, services.resolve_missing_item(
            order_id, payload.item_id, payload.resolution_type, payload.quantity,
            user=request.auth, code=payload.code, purchase_cost=payload.purchase_cost, note=payload.note,
        )
    except services.OrderFlowError as exc:
        return 400, {'detail': str(exc)}


@router.post('/{order_id}/clean-reception', response={200: LaundryOrderOut, 400: MessageOut})
@require_roles(User.Role.SUPERVISOR)
def confirm_clean_reception(request, order_id: int, payload: NoteIn):
    """El supervisor en faena confirma que el morral limpio llegó correcto (paso 8)."""
    try:
        return 200, services.confirm_clean_reception(order_id, user=request.auth, note=payload.note)
    except services.OrderFlowError as exc:
        return 400, {'detail': str(exc)}


@router.post('/{order_id}/deliver', response={200: LaundryOrderOut, 400: MessageOut})
@require_roles(User.Role.SUPERVISOR)
def register_delivery(request, order_id: int, payload: NoteIn):
    """Entrega del morral al trabajador en su habitación (paso 9, Flujo 1)."""
    try:
        return 200, services.register_delivery(order_id, user=request.auth, note=payload.note)
    except (services.OrderFlowError, services.InvalidStatusTransition) as exc:
        return 400, {'detail': str(exc)}


@router.post('/{order_id}/photo-upload-url', response=PhotoUploadOut)
def request_photo_upload(request, order_id: int, payload: PhotoUploadRequestIn):
    return services.request_photo_upload(order_id, payload.filename, payload.content_type)


@router.post('/{order_id}/photo-confirm', response=LaundryOrderOut)
def confirm_photo_upload(request, order_id: int, payload: PhotoConfirmIn):
    services.confirm_photo_upload(order_id, payload.object_key)
    return services.get_order(order_id)
