from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import Count, Q, QuerySet, Sum
from django.utils import timezone

from authentication.models import User
from companies.models import Company
from hospitality.models import BatchStatus, LinenBatch, LinenBatchItem
from hospitality.schemas import LinenBatchIn, LinenBatchItemIn, ReturnCountIn

# Días de proceso en planta antes de devolver la carga limpia. Es más que el de
# una guía de trabajador (3): una carga de hotelería son cientos de piezas y no
# compite por la fecha de salida de nadie a faena.
DEFAULT_TURNAROUND_DAYS = 4

# Prefijo del correlativo de lote. Los lotes no comparten numeración con las
# guías: son otro servicio y se informan por separado al mandante.
BATCH_PREFIX = 'H'


class BatchFlowError(Exception):
    """El lote no está en el punto del flujo que exige esta operación."""


def generate_batch_number(moment: datetime | None = None) -> str:
    """Correlativo anual del lote: `H-2026-0001`.

    Anual y no semanal —a diferencia del `ref` de las guías, que se resetea cada
    semana y por eso se repite en el histórico—: los lotes son pocos (unos tres
    por envío, un par de envíos al mes) y conviene que su número sea único para
    siempre, porque es el que se le informa al mandante.
    """
    moment = moment or timezone.now()
    year = timezone.localtime(moment).year
    last = (
        LinenBatch.objects.filter(batch_number__startswith=f'{BATCH_PREFIX}-{year}-')
        .order_by('-batch_number')
        .values_list('batch_number', flat=True)
        .first()
    )
    sequence = int(last.rsplit('-', 1)[1]) + 1 if last else 1
    return f'{BATCH_PREFIX}-{year}-{sequence:04d}'


def calculate_promised_at(reference: datetime) -> datetime:
    return reference + timedelta(days=DEFAULT_TURNAROUND_DAYS)


def _build_items(batch: LinenBatch, items: list[LinenBatchItemIn]) -> None:
    LinenBatchItem.objects.bulk_create(
        LinenBatchItem(
            batch=batch,
            garment_type_id=item.garment_type_id,
            custom_name=item.custom_name,
            quantity_in=item.quantity_in,
            weight_kg=item.weight_kg,
        )
        for item in items
    )


@transaction.atomic
def create_batch(payload: LinenBatchIn, received_by: User) -> LinenBatch:
    """Registra la llegada de una carga de lencería sucia desde el campamento."""
    company = Company.objects.get(pk=payload.company_id)
    if company.service_type != Company.ServiceType.HOSPITALITY:
        raise BatchFlowError(
            f'{company.name} no es un contrato de hotelería. '
            'La ropa de trabajadores se registra como guía, no como lote.'
        )
    if not payload.items:
        raise BatchFlowError('El lote debe declarar al menos un tipo de lencería.')

    received_at = payload.received_at or timezone.now()
    batch = LinenBatch.objects.create(
        batch_number=generate_batch_number(received_at),
        company=company,
        camp_id=payload.camp_id,
        received_at=received_at,
        promised_at=payload.promised_at or calculate_promised_at(received_at),
        weight_kg=payload.weight_kg,
        observations=payload.observations,
        received_by=received_by,
    )
    _build_items(batch, payload.items)
    return batch


def get_batch(batch_id: int) -> LinenBatch:
    return (
        LinenBatch.objects.select_related('company', 'company__client', 'camp', 'received_by', 'dispatched_by')
        .prefetch_related('items__garment_type')
        .get(pk=batch_id)
    )


def list_batches(
    status: str | None = None,
    company_id: int | None = None,
    search: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> QuerySet[LinenBatch]:
    queryset = (
        LinenBatch.objects.select_related('company', 'camp')
        .prefetch_related('items__garment_type')
        .all()
    )
    if status:
        queryset = queryset.filter(status=status)
    if company_id:
        queryset = queryset.filter(company_id=company_id)
    if search:
        queryset = queryset.filter(
            Q(batch_number__icontains=search) | Q(company__name__icontains=search)
        )
    if date_from:
        queryset = queryset.filter(received_at__gte=date_from)
    if date_to:
        queryset = queryset.filter(received_at__lte=date_to)
    return queryset


@transaction.atomic
def start_processing(batch_id: int, user: User) -> LinenBatch:
    """Marca que la carga entró a lavado."""
    batch = LinenBatch.objects.select_for_update().get(pk=batch_id)
    if batch.status != BatchStatus.RECEIVED:
        raise BatchFlowError(f'El lote {batch.batch_number} ya salió de recepción.')
    batch.status = BatchStatus.IN_PROCESS
    batch.save(update_fields=['status', 'updated_at'])
    return get_batch(batch_id)


@transaction.atomic
def register_return_count(batch_id: int, counts: list[ReturnCountIn], user: User) -> LinenBatch:
    """Cuenta de salida: cuántas piezas de cada tipo volvieron del lavado.

    Es el momento donde aparece la merma y por eso se puede repetir mientras el
    lote no se despache: contar cientos de sábanas admite corrección, y obligar
    a despachar para arreglar un número sería peor.
    """
    batch = LinenBatch.objects.select_for_update().get(pk=batch_id)
    if batch.status == BatchStatus.DISPATCHED:
        raise BatchFlowError(f'El lote {batch.batch_number} ya fue despachado.')

    items = {item.id: item for item in batch.items.all()}
    for count in counts:
        item = items.get(count.item_id)
        if item is None:
            raise BatchFlowError(f'La línea {count.item_id} no pertenece a este lote.')
        if count.quantity_out > item.quantity_in:
            raise BatchFlowError(
                f'{item.display_name}: no pueden volver {count.quantity_out} si entraron {item.quantity_in}.'
            )
        item.quantity_out = count.quantity_out

    LinenBatchItem.objects.bulk_update(items.values(), ['quantity_out'])
    if batch.status == BatchStatus.RECEIVED:
        batch.status = BatchStatus.IN_PROCESS
        batch.save(update_fields=['status', 'updated_at'])
    return get_batch(batch_id)


@transaction.atomic
def dispatch_batch(batch_id: int, user: User, received_by_client: str = '', note: str = '') -> LinenBatch:
    """Despacha la carga limpia de vuelta a faena y cierra el lote.

    Exige la cuenta de salida completa: despachar sin contar dejaría la merma
    sin registrar, que es justamente lo que este módulo viene a resolver.
    """
    batch = LinenBatch.objects.select_for_update().get(pk=batch_id)
    if batch.status == BatchStatus.DISPATCHED:
        raise BatchFlowError(f'El lote {batch.batch_number} ya fue despachado.')

    pending = [item.display_name for item in batch.items.all() if item.quantity_out is None]
    if pending:
        raise BatchFlowError(
            'Falta contar la salida de: ' + ', '.join(pending) + '.'
        )

    batch.status = BatchStatus.DISPATCHED
    batch.dispatched_at = timezone.now()
    batch.dispatched_by = user
    batch.received_by_client = received_by_client
    if note:
        batch.observations = f'{batch.observations}\n{note}'.strip()
    batch.save(
        update_fields=[
            'status', 'dispatched_at', 'dispatched_by', 'received_by_client', 'observations', 'updated_at'
        ]
    )
    return get_batch(batch_id)


def build_batch_summary(batch: LinenBatch) -> dict:
    """Totales del lote: lo que entró, lo que volvió y la merma."""
    items = list(batch.items.all())
    total_in = sum(item.quantity_in for item in items)
    counted = [item for item in items if item.quantity_out is not None]
    total_out = sum(item.quantity_out for item in counted)
    return {
        'total_in': total_in,
        'total_out': total_out if counted else None,
        'shortage': total_in - total_out if len(counted) == len(items) and items else None,
        'is_counted': bool(items) and len(counted) == len(items),
    }


def get_hospitality_counters(date_from: datetime | None = None, date_to: datetime | None = None) -> dict:
    """Indicadores del servicio de hotelería para el panel del módulo."""
    queryset = LinenBatch.objects.all()
    if date_from:
        queryset = queryset.filter(received_at__gte=date_from)
    if date_to:
        queryset = queryset.filter(received_at__lte=date_to)

    counters = queryset.aggregate(
        batches=Count('id'),
        in_plant=Count('id', filter=Q(status__in=[BatchStatus.RECEIVED, BatchStatus.IN_PROCESS])),
        dispatched=Count('id', filter=Q(status=BatchStatus.DISPATCHED)),
        weight_kg=Sum('weight_kg'),
    )
    totals = LinenBatchItem.objects.filter(batch__in=queryset).aggregate(
        pieces_in=Sum('quantity_in'), pieces_out=Sum('quantity_out')
    )
    pieces_in = totals['pieces_in'] or 0
    # Solo se compara contra las líneas ya contadas: mezclar lo que entró de un
    # lote en proceso con lo que salió de otro despachado inventaría una merma
    # que no existe.
    counted = LinenBatchItem.objects.filter(batch__in=queryset, quantity_out__isnull=False).aggregate(
        pieces_in=Sum('quantity_in'), pieces_out=Sum('quantity_out')
    )
    counted_in = counted['pieces_in'] or 0
    counted_out = counted['pieces_out'] or 0
    return {
        'batches': counters['batches'],
        'in_plant': counters['in_plant'],
        'dispatched': counters['dispatched'],
        'weight_kg': float(counters['weight_kg']) if counters['weight_kg'] is not None else None,
        'pieces_in': pieces_in,
        'pieces_out': totals['pieces_out'] or 0,
        'shortage': counted_in - counted_out,
        'shortage_rate': round((counted_in - counted_out) / counted_in, 4) if counted_in else None,
    }


def build_batch_note(batch_id: int) -> dict:
    """Acta de devolución del lote: el equivalente a la boleta, para el mandante.

    No lleva trabajador, RUT ni habitación —no existen en hotelería— y en cambio
    lleva el conteo por tipo de lencería con su merma, que es lo que el
    encargado del campamento revisa y firma al recibir la carga.
    """
    batch = get_batch(batch_id)
    summary = build_batch_summary(batch)
    return {
        'batch_number': batch.batch_number,
        'company_name': batch.company.name,
        'company_logo_url': _company_logo_url(batch.company),
        'camp': batch.camp.name if batch.camp_id else '',
        'status': batch.status,
        'received_at': batch.received_at,
        'promised_at': batch.promised_at,
        'dispatched_at': batch.dispatched_at,
        'weight_kg': float(batch.weight_kg) if batch.weight_kg is not None else None,
        'received_by_client': batch.received_by_client,
        'observations': batch.observations,
        'items': [
            {
                'item_id': item.id,
                'name': item.display_name,
                'quantity_in': item.quantity_in,
                'quantity_out': item.quantity_out,
                'shortage': item.shortage,
            }
            for item in batch.items.all()
        ],
        **summary,
    }


def _company_logo_url(company: Company) -> str | None:
    from common.services import build_object_url

    return build_object_url(company.logo_key)
