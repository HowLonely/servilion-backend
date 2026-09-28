from collections import defaultdict
from datetime import datetime

from django.db import IntegrityError, transaction
from django.db.models import QuerySet
from django.utils import timezone

from authentication.models import User
from camps.models import Camp
from companies.models import Company
from garments.models import GarmentType
from hospitality.models import DispatchCounter, LinenLocation, LinenMovement, LinenMovementLine
from hospitality.schemas import CountLineIn, FieldMovementIn, LinenLineIn

Kind = LinenMovement.Kind

# Prefijo del número de despacho. Los despachos no comparten numeración con las
# guías de trabajadores: son otro servicio y se informan por separado.
DISPATCH_PREFIX = 'HD'

# Tope de cordura por línea, como en la báscula: no es una regla del negocio,
# es un cortafuegos contra un cero de más tecleado en una pantalla táctil.
MAX_LINE_QUANTITY = 20_000

# Cómo mueve cada flujo la lencería entre lugares: (lugar, signo). El lugar
# CAMPAMENTO se resuelve con el `camp_id` del movimiento.
FLOW_EFFECTS: dict[str, tuple[tuple[str, int], ...]] = {
    Kind.DISPATCH: ((LinenLocation.SERVILION, -1), (LinenLocation.FAENA, +1)),
    Kind.DISTRIBUTION: ((LinenLocation.FAENA, -1), (LinenLocation.CAMP, +1)),
    Kind.COLLECTION: ((LinenLocation.CAMP, -1), (LinenLocation.SERVILION, +1)),
}

FIELD_KINDS = (Kind.DISTRIBUTION, Kind.COLLECTION)


class LinenFlowError(Exception):
    """Regla de negocio de hotelería violada (se traduce a 400 en la API)."""


# --- Consultas ----------------------------------------------------------------


def _base_queryset() -> QuerySet[LinenMovement]:
    return LinenMovement.objects.select_related(
        'company', 'company__client', 'company__client__faena', 'camp', 'registered_by', 'voided_by'
    ).prefetch_related('lines__garment_type')


def get_movement(movement_id: int) -> LinenMovement:
    return _base_queryset().get(pk=movement_id)


def list_movements(
    company_id: int | None = None,
    camp_id: int | None = None,
    kind: str | None = None,
    include_voided: bool = True,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> QuerySet[LinenMovement]:
    queryset = _base_queryset()
    if company_id:
        queryset = queryset.filter(company_id=company_id)
    if camp_id:
        queryset = queryset.filter(camp_id=camp_id)
    if kind:
        queryset = queryset.filter(kind=kind)
    if not include_voided:
        queryset = queryset.filter(voided_at__isnull=True)
    if date_from:
        queryset = queryset.filter(occurred_at__gte=date_from)
    if date_to:
        queryset = queryset.filter(occurred_at__lte=date_to)
    return queryset


def hospitality_companies() -> QuerySet[Company]:
    return (
        Company.objects.filter(service_type=Company.ServiceType.HOSPITALITY, is_active=True)
        .select_related('client', 'client__faena')
        .order_by('name')
    )


def camps_of(company: Company) -> QuerySet[Camp]:
    """Campamentos a los que puede ir la lencería del cliente: los de su faena."""
    if not company.client.faena_id:
        return Camp.objects.none()
    return Camp.objects.filter(faena_id=company.client.faena_id, is_active=True).order_by('name')


# --- Saldos -------------------------------------------------------------------


def _location_key(kind: str, camp_id: int | None) -> tuple[str, int | None]:
    return (kind, camp_id if kind == LinenLocation.CAMP else None)


def _balances_by_key(company_id: int) -> tuple[dict, dict]:
    """Saldo por (lugar, tipo) y fecha del último conteo por lugar.

    El CONTEO fija el saldo: desde el último conteo de un lugar y tipo, se
    suman solo los flujos ocurridos DESPUÉS. Los anteriores ya están dentro de
    lo contado. Es lo que hace correcto a un retiro registrado sin señal que
    sincroniza después del conteo: si ocurrió antes, la persona que contó ya no
    vio esas sábanas, y restarlas de nuevo las descontaría dos veces.
    """
    lines = (
        LinenMovementLine.objects.filter(movement__company_id=company_id, movement__voided_at__isnull=True)
        .values(
            'movement_id',
            'movement__kind',
            'movement__camp_id',
            'movement__occurred_at',
            'garment_type_id',
            'quantity',
        )
    )

    last_counts: dict[tuple, tuple] = {}
    flows = []
    for line in lines:
        stamp = (line['movement__occurred_at'], line['movement_id'])
        if line['movement__kind'] == Kind.COUNT:
            location = (
                _location_key(LinenLocation.CAMP, line['movement__camp_id'])
                if line['movement__camp_id']
                else _location_key(LinenLocation.FAENA, None)
            )
            key = (location, line['garment_type_id'])
            if key not in last_counts or stamp > last_counts[key][0]:
                last_counts[key] = (stamp, line['quantity'])
        else:
            flows.append((stamp, line))

    balances: dict[tuple, int] = defaultdict(int)
    last_counted_at: dict[tuple, datetime] = {}
    for (location, garment_type_id), (stamp, quantity) in last_counts.items():
        balances[(location, garment_type_id)] = quantity
        if location not in last_counted_at or stamp[0] > last_counted_at[location]:
            last_counted_at[location] = stamp[0]

    for stamp, line in flows:
        for location_kind, sign in FLOW_EFFECTS[line['movement__kind']]:
            location = _location_key(location_kind, line['movement__camp_id'])
            key = (location, line['garment_type_id'])
            counted = last_counts.get(key)
            if counted is not None and stamp <= counted[0]:
                continue
            balances[key] += sign * line['quantity']

    return balances, last_counted_at


def compute_balances(company: Company) -> dict:
    """Dónde está la lencería del cliente: Servilion, bodega de faena y campamentos."""
    balances, last_counted_at = _balances_by_key(company.id)

    used_type_ids = {garment_type_id for (_, garment_type_id) in balances}
    linen_types = list(
        GarmentType.objects.filter(is_linen=True, is_active=True) | GarmentType.objects.filter(id__in=used_type_ids)
    )
    linen_types.sort(key=lambda garment_type: garment_type.code)

    camps = list(camps_of(company))
    # Un campamento desactivado que todavía tiene lencería sigue apareciendo:
    # esconderlo haría desaparecer piezas del saldo sin que nadie las cuente.
    known = {camp.id for camp in camps}
    orphan_ids = {
        location[1]
        for (location, _), quantity in balances.items()
        if location[0] == LinenLocation.CAMP and location[1] not in known and quantity != 0
    }
    camps += list(Camp.objects.filter(id__in=orphan_ids).order_by('name'))

    rows = [
        (LinenLocation.SERVILION, None, LinenLocation.SERVILION.label),
        (LinenLocation.FAENA, None, LinenLocation.FAENA.label),
        *((LinenLocation.CAMP, camp.id, camp.name) for camp in camps),
    ]
    locations = []
    for kind, camp_id, name in rows:
        location = _location_key(kind, camp_id)
        lines = [
            {'garment_type_id': garment_type.id, 'quantity': balances.get((location, garment_type.id), 0)}
            for garment_type in linen_types
        ]
        locations.append({
            'kind': kind,
            'camp_id': camp_id,
            'name': name,
            'lines': lines,
            'total': sum(line['quantity'] for line in lines),
            'has_negative': any(line['quantity'] < 0 for line in lines),
            'last_counted_at': last_counted_at.get(location),
        })

    return {
        'company_id': company.id,
        'company_name': company.name,
        'faena_name': company.faena_name,
        'linen_types': linen_types,
        'locations': locations,
    }


def compute_all_balances(company_id: int | None = None) -> list[dict]:
    companies = hospitality_companies()
    if company_id:
        companies = companies.filter(pk=company_id)
    return [compute_balances(company) for company in companies]


# --- Validaciones comunes -------------------------------------------------------


def _hospitality_company(company_id: int, lock: bool = False) -> Company:
    queryset = Company.objects.select_related('client', 'client__faena')
    if lock:
        # Serializa los movimientos de un mismo cliente: el conteo calcula su
        # diferencia contra el saldo, y ese saldo no puede cambiar a mitad.
        queryset = queryset.select_for_update(of=('self',))
    company = queryset.get(pk=company_id)
    if company.service_type != Company.ServiceType.HOSPITALITY:
        raise LinenFlowError(
            f'{company.name} no es un contrato de hotelería. '
            'La ropa de trabajadores se registra como guía.'
        )
    return company


def _camp_of(company: Company, camp_id: int) -> Camp:
    camp = Camp.objects.get(pk=camp_id)
    if company.client.faena_id and camp.faena_id != company.client.faena_id:
        raise LinenFlowError(
            f'El campamento {camp.name} no pertenece a la faena de {company.name}.'
        )
    return camp


def _linen_types(garment_type_ids: list[int]) -> dict[int, GarmentType]:
    if len(set(garment_type_ids)) != len(garment_type_ids):
        raise LinenFlowError('Un mismo tipo de lencería viene dos veces en el movimiento.')
    found = {gt.id: gt for gt in GarmentType.objects.filter(id__in=garment_type_ids)}
    missing = set(garment_type_ids) - set(found)
    if missing:
        raise LinenFlowError('Hay tipos de lencería que no existen en el catálogo.')
    not_linen = [gt.name for gt in found.values() if not gt.is_linen]
    if not_linen:
        raise LinenFlowError(
            'No están marcados como lencería de hotelería: ' + ', '.join(sorted(not_linen)) + '.'
        )
    return found


def _validate_flow_lines(lines: list[LinenLineIn]) -> dict[int, GarmentType]:
    if not lines:
        raise LinenFlowError('El movimiento debe traer al menos un tipo de lencería.')
    for line in lines:
        if line.quantity < 1:
            raise LinenFlowError('Cada línea debe mover al menos una pieza.')
        if line.quantity > MAX_LINE_QUANTITY:
            raise LinenFlowError(
                f'{line.quantity} piezas en una sola línea no es plausible '
                f'(el máximo es {MAX_LINE_QUANTITY}). Revisa el número tecleado.'
            )
    return _linen_types([line.garment_type_id for line in lines])


# --- Movimientos ----------------------------------------------------------------


def _next_dispatch_number(moment: datetime) -> str:
    year = timezone.localtime(moment).year
    counter, _ = DispatchCounter.objects.select_for_update().get_or_create(year=year)
    counter.last_number += 1
    counter.save(update_fields=['last_number'])
    return f'{DISPATCH_PREFIX}-{year}-{counter.last_number:04d}'


@transaction.atomic
def register_dispatch(company_id: int, lines: list[LinenLineIn], user: User, note: str = '') -> LinenMovement:
    """Despacho de lencería limpia desde la planta hacia la faena del cliente."""
    company = _hospitality_company(company_id, lock=True)
    _validate_flow_lines(lines)
    occurred_at = timezone.now()

    movement = LinenMovement.objects.create(
        kind=Kind.DISPATCH,
        company=company,
        number=_next_dispatch_number(occurred_at),
        occurred_at=occurred_at,
        registered_by=user,
        note=note,
    )
    LinenMovementLine.objects.bulk_create(
        LinenMovementLine(movement=movement, garment_type_id=line.garment_type_id, quantity=line.quantity)
        for line in lines
    )
    return get_movement(movement.id)


@transaction.atomic
def register_count(
    company_id: int,
    camp_id: int | None,
    lines: list[CountLineIn],
    user: User,
    note: str = '',
) -> LinenMovement:
    """Conteo de inventario de un lugar: carga inicial o reajuste.

    Deja el saldo en lo contado. La diferencia contra lo que el sistema creía
    queda anotada por línea para ver de cuánto fue el ajuste.
    """
    company = _hospitality_company(company_id, lock=True)
    camp = _camp_of(company, camp_id) if camp_id else None
    if not lines:
        raise LinenFlowError('El conteo debe traer al menos un tipo de lencería.')
    for line in lines:
        if line.counted < 0:
            raise LinenFlowError('Lo contado no puede ser negativo.')
        if line.counted > MAX_LINE_QUANTITY:
            raise LinenFlowError(
                f'{line.counted} piezas no es plausible (el máximo es {MAX_LINE_QUANTITY}).'
            )
    _linen_types([line.garment_type_id for line in lines])

    balances, _ = _balances_by_key(company.id)
    location = (
        _location_key(LinenLocation.CAMP, camp.id) if camp else _location_key(LinenLocation.FAENA, None)
    )

    movement = LinenMovement.objects.create(
        kind=Kind.COUNT,
        company=company,
        camp=camp,
        occurred_at=timezone.now(),
        registered_by=user,
        note=note,
    )
    LinenMovementLine.objects.bulk_create(
        LinenMovementLine(
            movement=movement,
            garment_type_id=line.garment_type_id,
            quantity=line.counted,
            difference=line.counted - balances.get((location, line.garment_type_id), 0),
        )
        for line in lines
    )
    return get_movement(movement.id)


def _register_field_movement(payload: FieldMovementIn, user: User) -> LinenMovement:
    if payload.kind not in FIELD_KINDS:
        raise LinenFlowError(f'Tipo de movimiento de terreno desconocido: {payload.kind}.')

    company = _hospitality_company(payload.company_id, lock=True)
    camp = _camp_of(company, payload.camp_id)
    _validate_flow_lines(payload.lines)
    # Un teléfono con la hora adelantada no puede registrar algo en el futuro:
    # el orden contra los conteos depende de este momento.
    occurred_at = min(payload.occurred_at, timezone.now())

    movement = LinenMovement.objects.create(
        client_uuid=payload.client_uuid,
        kind=payload.kind,
        company=company,
        camp=camp,
        occurred_at=occurred_at,
        registered_by=user,
        note=payload.note,
        latitude=payload.latitude,
        longitude=payload.longitude,
        accuracy_meters=payload.accuracy_meters,
    )
    LinenMovementLine.objects.bulk_create(
        LinenMovementLine(movement=movement, garment_type_id=line.garment_type_id, quantity=line.quantity)
        for line in payload.lines
    )
    return movement


def sync_field_movement(payload: FieldMovementIn, user: User) -> dict:
    """Registra un reparto o retiro de la cola del teléfono, de forma idempotente.

    Nunca se rechaza porque el campamento quede en negativo: en terreno se
    registra lo que se contó. El saldo negativo queda marcado en la consulta
    para que un administrador haga un conteo.
    """
    existing = LinenMovement.objects.filter(client_uuid=payload.client_uuid).first()
    if existing is not None:
        return {'client_uuid': payload.client_uuid, 'status': 'DUPLICADO', 'movement_id': existing.id}

    try:
        with transaction.atomic():
            movement = _register_field_movement(payload, user)
    except IntegrityError:
        # Dos envíos del mismo movimiento que llegaron a la vez: el otro ganó.
        existing = LinenMovement.objects.filter(client_uuid=payload.client_uuid).first()
        if existing is None:
            raise
        return {'client_uuid': payload.client_uuid, 'status': 'DUPLICADO', 'movement_id': existing.id}
    except (LinenFlowError, Company.DoesNotExist, Camp.DoesNotExist) as error:
        detail = str(error) if isinstance(error, LinenFlowError) else 'La empresa o el campamento no existen.'
        return {'client_uuid': payload.client_uuid, 'status': 'ERROR', 'detail': detail}

    return {'client_uuid': payload.client_uuid, 'status': 'CREADO', 'movement_id': movement.id}


@transaction.atomic
def void_movement(movement_id: int, user: User, reason: str) -> LinenMovement:
    """Anula un movimiento mal registrado. No se borra: queda en el historial."""
    reason = reason.strip()
    if not reason:
        raise LinenFlowError('Indica por qué se anula el movimiento.')

    movement = LinenMovement.objects.select_for_update().get(pk=movement_id)
    if movement.is_voided:
        raise LinenFlowError('El movimiento ya estaba anulado.')

    movement.voided_at = timezone.now()
    movement.voided_by = user
    movement.void_reason = reason[:200]
    movement.save(update_fields=['voided_at', 'voided_by', 'void_reason', 'updated_at'])
    return get_movement(movement.id)


def build_dispatch_print_job(movement_id: int) -> dict:
    """Datos de la guía de despacho que imprime la etiquetera de la planta."""
    movement = get_movement(movement_id)
    if movement.kind != Kind.DISPATCH:
        raise LinenFlowError('Solo los despachos tienen guía impresa.')
    if movement.is_voided:
        raise LinenFlowError(f'El despacho {movement.number} está anulado.')

    lines = [
        {'code': line.garment_type.code, 'name': line.garment_type.name, 'quantity': line.quantity}
        for line in movement.lines.all()
    ]
    user = movement.registered_by
    return {
        'number': movement.number,
        'company_name': movement.company.name,
        'faena': movement.company.client.faena.name if movement.company.client.faena_id else '',
        'occurred_at': movement.occurred_at,
        'registered_by_name': (user.get_full_name() or user.username) if user else '',
        'note': movement.note,
        'total_quantity': sum(line['quantity'] for line in lines),
        'lines': lines,
    }
