import re
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from django.db import transaction
from django.db.models import Count, Q, QuerySet
from django.utils import timezone

from authentication.models import User
from authentication.permissions import PermissionDenied, user_has_role
from common.services import build_object_url, build_presigned_upload
from companies.models import Client, Company
from companies.services import get_client_price_map
from camps import services as camps_services
from camps.models import Room
from garments.models import GarmentType
from orders.models import (
    REFERENCE_SEQUENCE_END,
    REFERENCE_SEQUENCE_START,
    LaundryOrder,
    MissingItemResolution,
    OrderItem,
    OrderStatus,
    OrderStatusHistory,
    ReferenceCounter,
    SiteScan,
    SyncConflict,
)
from orders.schemas import LaundryOrderIn, OrderItemIn, OrderSyncIn
from workers.models import Worker

# Transiciones que el staff puede declarar a mano desde el botón "Marcar
# como X" del panel. RECIBIDA -> EN_REVISION, EN_REVISION/INCOMPLETA ->
# COMPLETADA y COMPLETADA/INCOMPLETA -> DESPACHADA no aparecen aquí a
# propósito: el sistema las decide solo, como efecto de digitalizar la guía
# (`create_order`), de pistolear el empaque (`scan_packed_garment` /
# `finish_packing`), de resolver una prenda faltante (`resolve_missing_item`) y
# de pistolear la boleta del morral ya cerrado (`dispatch_order`) — ver
# `_advance_status`.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    OrderStatus.RECEIVED: set(),
    OrderStatus.QUALITY_CHECK: set(),
    OrderStatus.INCOMPLETE: set(),
    # Cerrado no es despachado: el morral sigue en planta hasta que la boleta
    # se pistolea por tercera vez, así que la entrega ya no cuelga de aquí.
    OrderStatus.COMPLETED: set(),
    OrderStatus.DISPATCHED: {OrderStatus.DELIVERED},
    OrderStatus.DELIVERED: set(),
}

# Quién puede llevar la guía a cada estado manual. Refleja la separación de
# funciones de la operación real: quien digitaliza no factura, y el supervisor
# no toca el dinero. ADMIN atraviesa todo (ver `user_has_role`). Los estados no
# listados los puede mover cualquier staff autenticado.
STATUS_REQUIRED_ROLES: dict[str, tuple[str, ...]] = {
    OrderStatus.DELIVERED: (User.Role.SUPERVISOR,),
}

# Campo de timestamp que se completa automáticamente al entrar a cada estado.
STATUS_TIMESTAMP_FIELD: dict[str, str] = {
    OrderStatus.INCOMPLETE: 'incomplete_at',
    OrderStatus.COMPLETED: 'completed_at',
    OrderStatus.DISPATCHED: 'dispatched_at',
    OrderStatus.DELIVERED: 'delivered_at',
}

# Días de proceso en planta antes de que el morral esté listo para entregar.
# Es el valor por defecto cuando el turno del trabajador no permite acotar más.
DEFAULT_TURNAROUND_DAYS = 3

# Turnos escritos como "7X7", "14x14", "4X3": el primer número son los días en
# faena, el segundo los días de descanso fuera de ella.
SHIFT_PATTERN = re.compile(r'^\s*(\d{1,2})\s*[xX]\s*(\d{1,2})\s*$')

# Separador entre el `ref` de la guía y el código de prenda en la etiqueta
# lavable: `P1005-TOA`. El `ref` que genera `generate_reference` nunca lo trae,
# así que su presencia es lo que distingue una etiqueta de prenda del código de
# la boleta del morral (ver `split_garment_label`).
GARMENT_LABEL_SEPARATOR = '-'

# Prefijo del correlativo de etiqueta de las prendas fuera de catálogo, que no
# tienen código propio en `GarmentType`.
CUSTOM_LABEL_PREFIX = 'X'

# Estados en que el morral sigue en planta y su `ref` puede pistolearse. El
# número del ref se reutiliza al completar un ciclo (ver `ReferenceCounter`), y
# las etiquetas legadas ni siquiera traen letra de ciclo: acotar a las guías
# todavía abiertas es lo que vuelve el código resoluble sin pedirle al operador
# ningún dato que la etiqueta no muestre.
OPEN_PACKING_STATUSES = (OrderStatus.RECEIVED, OrderStatus.QUALITY_CHECK, OrderStatus.INCOMPLETE)

# Estados en que el morral ya se cerró pero sigue en planta, esperando el
# pistoleo que lo despacha. INCOMPLETA está en ambas tuplas a propósito: es el
# único estado que acepta las dos cosas —la etiqueta de una prenda que reaparece
# lo resuelve, la boleta lo despacha tal como está— y el separador de la
# etiqueta lavable es lo que distingue un caso del otro (ver `scan_packing_code`).
CLOSED_PACKING_STATUSES = (OrderStatus.COMPLETED, OrderStatus.INCOMPLETE)

# Estados en que un pistoleo de prenda todavía resuelve un faltante. DESPACHADA
# entra porque el morral puede haber salido incompleto: la prenda que aparece
# después viaja en un envío aparte, caso que `confirm_clean_reception` ya
# contempla al ser repetible. Se acota con `_is_resolving_missing`, que exige
# que quede algo pendiente de verdad.
RESOLVABLE_STATUSES = (OrderStatus.INCOMPLETE, OrderStatus.DISPATCHED)

# Estados en que la boleta puede despachar algo. DESPACHADA entra porque el
# despacho es repetible: tras el morral pueden salir, en envíos aparte, las
# prendas que se resolvieron después (ver `unshipped_resolutions`). Solo el
# primero mueve el estado; los siguientes son envíos de la misma guía.
DISPATCHABLE_STATUSES = CLOSED_PACKING_STATUSES + (OrderStatus.DISPATCHED,)

# Cómo se valida el morral en la mesa de empaque. UNIT es el modo nuevo: la
# guía viene de un pesaje y cada prenda física trae su propio adhesivo
# (`P1375A-03`), así que se cuentan unidades únicas. TYPE es el modo histórico,
# que sigue vivo para las guías digitalizadas sin pasar por la báscula: el
# adhesivo identifica el TIPO de prenda y se pistolea tantas veces como
# unidades vuelvan.
PACKING_MODE_UNIT = 'unidad'
PACKING_MODE_TYPE = 'tipo'

# Resultado de `scan_packing_code`, para que la UI sepa qué acaba de pasar
# físicamente con el morral.
PACKING_ACTION_OPENED = 'ABIERTO'
PACKING_ACTION_CLOSED = 'CERRADO'
PACKING_ACTION_SCANNED = 'PISTOLEADA'
# La prenda que faltaba reapareció: se pistoleó sobre una guía ya Incompleta.
PACKING_ACTION_FOUND = 'ENCONTRADA'


class InvalidStatusTransition(Exception):
    pass


class OrderFlowError(Exception):
    """La guía no está en el punto del flujo que exige esta operación."""


def parse_shift(shift: str) -> tuple[int, int] | None:
    """Descompone un turno "NxM" en (días en faena, días fuera). None si no calza."""
    match = SHIFT_PATTERN.match(shift or '')
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def calculate_promised_at(shift: str, reference: datetime) -> datetime:
    """Fecha tentativa de entrega impresa en la boleta (`entrega` en Access).

    El turno la condiciona: si al trabajador le quedan menos días en faena que
    los que demora el proceso, la ropa debe volver antes de que se vaya, porque
    si no no habrá nadie a quien entregársela hasta el ciclo siguiente.

    Es una aproximación deliberada: el sistema no conoce en qué día del ciclo
    está cada trabajador (ver decisiones pendientes en FLUJO_NEGOCIO.md §10).
    """
    parsed = parse_shift(shift)
    if parsed is None:
        return reference + timedelta(days=DEFAULT_TURNAROUND_DAYS)
    on_site_days, _ = parsed
    return reference + timedelta(days=min(DEFAULT_TURNAROUND_DAYS, max(on_site_days - 1, 1)))


def next_cycle(cycle: str) -> str:
    """Avanza la letra de ciclo: A → B → … → Z → AA → AB.

    Es un contador en base 26 con letras, no un simple `chr(ord+1)`: al pasar de
    Z tiene que seguir contando en vez de salirse del alfabeto.
    """
    letters = list(cycle.upper() or 'A')
    position = len(letters) - 1
    while position >= 0:
        if letters[position] != 'Z':
            letters[position] = chr(ord(letters[position]) + 1)
            return ''.join(letters)
        letters[position] = 'A'
        position -= 1
    return 'A' + ''.join(letters)


@transaction.atomic
def generate_reference(client: Client) -> str:
    """Genera el `ref` operativo del cliente: `P1375A`.

    Prefijo del cliente al que se le factura, correlativo de 4 dígitos y letra
    de ciclo. El correlativo no se reinicia por calendario: corre de 1000 a 1999
    y al dar la vuelta avanza la letra, que es lo que evita que el mismo código
    identifique a dos morrales vivos al mismo tiempo.

    El prefijo es del cliente y no de la empresa porque el ref identifica a
    quién se le factura: todas las contratistas del cliente comparten contador,
    igual que en el sistema antiguo, donde un solo `P` cubría a todas las
    empresas de Peñón.

    El `select_for_update` serializa la generación entre digitadores
    concurrentes para que dos guías nunca compartan ref.
    """
    prefix = (client.reference_prefix or client.name[:1]).upper()

    counter, created = ReferenceCounter.objects.select_for_update().get_or_create(prefix=prefix)
    if not created:
        counter.last_number += 1
        if counter.last_number > REFERENCE_SEQUENCE_END:
            counter.last_number = REFERENCE_SEQUENCE_START
            counter.cycle = next_cycle(counter.cycle)
        counter.save(update_fields=['last_number', 'cycle'])
    return f'{prefix}{counter.last_number}{counter.cycle}'


def build_label_codes(items: list[OrderItemIn]) -> list[str]:
    """Código de etiqueta lavable de cada línea, único dentro de la guía.

    Las prendas del catálogo usan su propio código (`TOA`), que es el que el
    operador reconoce de un vistazo al buscar qué falta. Las que van fuera de
    catálogo no tienen uno, así que reciben un correlativo `X1`, `X2`.
    """
    catalog_codes = dict(
        GarmentType.objects.filter(
            id__in=[item.garment_type_id for item in items if item.garment_type_id]
        ).values_list('id', 'code')
    )
    label_codes = []
    custom_sequence = 0
    for item in items:
        if item.garment_type_id:
            label_codes.append(catalog_codes.get(item.garment_type_id, ''))
        else:
            custom_sequence += 1
            label_codes.append(f'{CUSTOM_LABEL_PREFIX}{custom_sequence}')
    return label_codes


def _build_items(order: LaundryOrder, items: list[OrderItemIn]) -> int:
    label_codes = build_label_codes(items)
    OrderItem.objects.bulk_create(
        OrderItem(
            order=order,
            garment_type_id=item.garment_type_id,
            custom_name=item.custom_name,
            quantity=item.quantity,
            label_code=label_code,
        )
        for item, label_code in zip(items, label_codes)
    )
    return sum(item.quantity for item in items)


# El digitador ya usa "0" (o variantes) como convención de facto para "el
# trabajador no escribió OT en el papel físico": no tiene sentido pedirle que
# cambie de hábito. El sistema normaliza esa convención a None en vez de
# guardar "0" como si fuera un identificador real (evita el problema del
# importador legado, que por lo mismo tuvo que inventar sufijos "-L<id>").
NO_OT_VALUES = {'0', '00', '000000', ''}


def normalize_order_number(value: str) -> str | None:
    return None if value.strip() in NO_OT_VALUES else value.strip()


@transaction.atomic
def create_order(payload: LaundryOrderIn, received_by: User) -> LaundryOrder:
    """Digitalización de la guía en Antofagasta (FLUJO_NEGOCIO.md §4, paso 4).

    Es el primer touchpoint del sistema de trazabilidad: aquí se genera el
    `ref`, se calcula la fecha tentativa de entrega y se enlazan los pistoleos
    que la faena ya había registrado para este número de OT.
    """
    worker = Worker.objects.select_related('company', 'company__client').get(pk=payload.worker_id)
    shift = payload.shift or worker.shift
    laundry_received_at = payload.laundry_received_at or timezone.now()

    # Import local: `weighing.services` importa `generate_reference` de este
    # módulo, así que a nivel de módulo serían imports circulares. La dirección
    # natural de la dependencia es weighing -> orders (el pesaje necesita el
    # correlativo del ref); este es el único punto donde va al revés.
    from weighing import services as weighing_services

    weigh_in = weighing_services.get_weigh_in(payload.weigh_in_id) if payload.weigh_in_id else None

    order = LaundryOrder.objects.create(
        order_number=normalize_order_number(payload.order_number),
        ticket_number=payload.ticket_number,
        worker=worker,
        company=worker.company,
        shift=shift,
        # El peso lo puso la báscula; el digitador solo lo sobrescribe si tipea
        # uno distinto (mismo criterio que con las prendas: manda el digitador).
        weight_kg=payload.weight_kg if payload.weight_kg is not None else (weigh_in.weight_kg if weigh_in else None),
        received_at=payload.received_at,
        laundry_received_at=laundry_received_at,
        promised_at=payload.promised_at or calculate_promised_at(shift, laundry_received_at),
        observations=payload.observations,
        # El ref NO se genera cuando la guía viene de un pesaje: ya está impreso
        # en los adhesivos que andan pegados a la ropa desde la recepción.
        # Emitir uno nuevo dejaría el morral con dos identidades.
        reference=(weigh_in.reference if weigh_in else generate_reference(worker.company.client)),
        control_code=payload.control_code,
        # Congela el destino de la entrega: `worker.current_room` puede
        # cambiar mientras la ropa está en planta, pero este morral se entrega
        # donde vivía el trabajador cuando la entregó sucia.
        delivery_room_id=payload.delivery_room_id or worker.current_room_id,
        received_by=received_by,
    )
    order.garment_count = _build_items(order, payload.items)
    order.save(update_fields=['garment_count'])
    if weigh_in:
        # Marca el pesaje como digitalizado y lo enlaza. Va después de crear las
        # líneas para que un fallo en el detalle no deje el pesaje consumido:
        # todo esto corre dentro de la misma transacción.
        weighing_services.consume(weigh_in.id, order)
    _attach_pending_site_scans(order)
    return order


def _attach_pending_site_scans(order: LaundryOrder) -> None:
    """Enlaza a la guía recién digitalizada los pistoleos que faena hizo antes.

    En faena se pistoléa el morral sucio (paso 2) cuando la guía todavía no
    existe en la base de datos, así que el escaneo quedó huérfano esperando esta
    digitalización.

    No se busca por `reference`: el ref recién se genera aquí, en Antofagasta,
    y al reciclarse cada semana podría capturar el escaneo de otra guía.
    """
    codes = [code for code in (order.order_number, order.control_code) if code]
    pending = SiteScan.objects.filter(order__isnull=True, scanned_code__in=codes)
    if not pending.exists():
        return

    first_dirty_scan = pending.filter(kind=SiteScan.Kind.DIRTY_IN).order_by('scanned_at').first()
    pending.update(order=order)
    if first_dirty_scan is not None:
        order.site_received_at = first_dirty_scan.scanned_at
        order.site_received_by = first_dirty_scan.scanned_by
        order.save(update_fields=['site_received_at', 'site_received_by', 'updated_at'])


def list_orders(
    status: str | None = None,
    company_id: int | None = None,
    client_id: int | None = None,
    worker_id: int | None = None,
    search: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> QuerySet[LaundryOrder]:
    queryset = LaundryOrder.objects.select_related(
        'worker', 'company', 'company__client', 'delivery_room', 'delivery_room__camp', 'weigh_in'
    ).prefetch_related('items__garment_type')
    if status:
        queryset = queryset.filter(status=status)
    if company_id is not None:
        queryset = queryset.filter(company_id=company_id)
    # Filtro por cliente: agrega todas las empresas del cliente. En el caso
    # cliente=empresa (1:1) da el mismo resultado que filtrar por la empresa.
    if client_id is not None:
        queryset = queryset.filter(company__client_id=client_id)
    if worker_id is not None:
        queryset = queryset.filter(worker_id=worker_id)
    if search:
        queryset = queryset.filter(
            Q(order_number__icontains=search)
            | Q(reference__icontains=search)
            | Q(control_code__icontains=search)
            | Q(worker__full_name__icontains=search)
            | Q(worker__national_id__icontains=search)
        )
    if date_from is not None:
        queryset = queryset.filter(received_at__gte=date_from)
    if date_to is not None:
        queryset = queryset.filter(received_at__lte=date_to)
    return queryset


def get_order(order_id: int) -> LaundryOrder:
    return LaundryOrder.objects.select_related(
        'worker', 'worker__company', 'company', 'company__client', 'company__client__faena',
        'received_by', 'reviewed_by',
        'delivery_room', 'delivery_room__camp', 'delivery_room__camp__faena',
        # OneToOne inverso: sin él, cada guía consulta su pesaje por separado en
        # el detalle y en el progreso de empaque.
        'weigh_in',
    ).prefetch_related('items__garment_type', 'weigh_in__labels').get(pk=order_id)


def find_order_by_code(code: str) -> LaundryOrder:
    """Resuelve un código pistoleado a una guía.

    En faena y en planta se escanean indistintamente el n° de OT, el `ref` de
    las etiquetas lavables y el código de control de la boleta: las tres capas
    de trazabilidad de FLUJO_NEGOCIO.md §5.

    Se devuelve la coincidencia más reciente porque ninguno de los tres códigos
    es único a lo largo de todo el histórico: el `ref` de las guías anteriores al
    ciclo (`ReferenceCounter`) se reiniciaba cada semana, y el número de control
    viene del sistema legado sin garantía de unicidad. La guía que el operador
    tiene en la mano es siempre la última.

    Este atajo NO sirve para mover un morral: quien pistolea en la mesa de
    empaque va por `scan_packing_code`, que restringe a las guías vivas y ante
    dos candidatas devuelve 409 en vez de elegir por él. Acá se elige la más
    reciente porque la consulta de faena es de solo lectura.
    """
    code = code.strip()
    order = (
        LaundryOrder.objects.select_related(
            'worker', 'company', 'company__client', 'delivery_room', 'delivery_room__camp'
        )
        .prefetch_related('items__garment_type')
        .filter(Q(order_number=code) | Q(reference=code) | Q(control_code=code))
        .order_by('-received_at')
        .first()
    )
    if order is None:
        raise LaundryOrder.DoesNotExist(f'No existe una guía con el código "{code}".')
    return order


def get_status_history(order_id: int) -> QuerySet[OrderStatusHistory]:
    return OrderStatusHistory.objects.filter(order_id=order_id).select_related('changed_by')


def allowed_transitions(order: LaundryOrder) -> set[str]:
    return set(ALLOWED_TRANSITIONS.get(order.status, set()))


def _advance_status(order: LaundryOrder, new_status: str, user: User, note: str = '') -> None:
    """Mueve la guía a `new_status` y lo audita, sin gate de rol.

    Es el mecanismo interno que usan las transiciones automáticas
    (`create_order`, `scan_packed_garment`, `finish_packing`): el rol ya quedó
    validado por el endpoint físico que las disparó (digitalizar, pistolear),
    así que no tiene sentido volver a exigir el rol pensado para el botón
    manual "Marcar como X" (`update_status`).
    """
    previous_status = order.status
    order.status = new_status

    timestamp_field = STATUS_TIMESTAMP_FIELD.get(new_status)
    update_fields = ['status', 'updated_at']
    if timestamp_field:
        setattr(order, timestamp_field, timezone.now())
        update_fields.append(timestamp_field)
    if new_status == OrderStatus.QUALITY_CHECK:
        order.reviewed_by = user
        update_fields.append('reviewed_by')
    if new_status == OrderStatus.DISPATCHED:
        order.dispatched_by = user
        update_fields.append('dispatched_by')
    if new_status == OrderStatus.DELIVERED:
        order.delivered_by = user
        update_fields.append('delivered_by')

    order.save(update_fields=update_fields)
    OrderStatusHistory.objects.create(
        order=order, previous_status=previous_status, new_status=new_status, changed_by=user, note=note
    )


@transaction.atomic
def update_status(order_id: int, new_status: str, user: User, note: str = '') -> LaundryOrder:
    """Transición manual disparada por el botón "Marcar como X" del panel.

    Solo cubre los pasos sin escaneo físico. Hoy queda una sola: DESPACHADA ->
    ENTREGADA para el Flujo 1 cuando la entrega no entra por la app. Todo lo
    demás tiene un pistoleo detrás — ver el comentario de `ALLOWED_TRANSITIONS`.
    """
    order = LaundryOrder.objects.select_for_update().select_related('company').get(pk=order_id)
    if new_status not in allowed_transitions(order):
        raise InvalidStatusTransition(f'No se puede pasar de {order.status} a {new_status}.')

    required_roles = STATUS_REQUIRED_ROLES.get(new_status)
    if required_roles and not user_has_role(user, *required_roles):
        raise PermissionDenied(
            f'Tu rol ({user.get_role_display()}) no puede marcar una guía como '
            f'{OrderStatus(new_status).label}.'
        )

    _advance_status(order, new_status, user, note)
    return order


# --- Paso 2: pistoleo de recepción de ropa sucia en faena (app PEÑON) ---


@transaction.atomic
def register_site_reception(code: str, user: User, scanned_at: datetime | None = None, note: str = '') -> SiteScan:
    """Registra el pistoleo del morral sucio en faena.

    La guía puede no existir todavía (se digitaliza recién en Antofagasta), así
    que el escaneo se guarda por código y queda pendiente de enlace.
    """
    scanned_at = scanned_at or timezone.now()
    try:
        order = find_order_by_code(code)
    except LaundryOrder.DoesNotExist:
        order = None

    scan = SiteScan.objects.create(
        kind=SiteScan.Kind.DIRTY_IN, scanned_code=code.strip(), order=order, scanned_at=scanned_at,
        scanned_by=user, note=note,
    )
    if order is not None and order.site_received_at is None:
        order.site_received_at = scanned_at
        order.site_received_by = user
        order.save(update_fields=['site_received_at', 'site_received_by', 'updated_at'])
    return scan


# La recepción en lavandería (`rlavanderia`) NO es una acción aparte: ocurre al
# digitalizar/ingresar la guía. `create_order` fija `laundry_received_at` y
# genera el `ref` en ese mismo momento, así que no hay un endpoint manual de
# "registrar recepción en lavandería".


# --- Paso 6: pistoleo de empaque del morral limpio ---


def order_units(order: LaundryOrder):
    """Adhesivos por unidad de esta guía, o vacío si no vino de un pesaje.

    Es lo que distingue los dos modos de empaque: con adhesivos por unidad se
    valida prenda física por prenda física; sin ellos se sigue validando por
    tipo, como las guías anteriores a la báscula.
    """
    weigh_in = getattr(order, 'weigh_in', None)
    if weigh_in is None:
        return []
    return list(weigh_in.labels.all())


def _packing_progress(order: LaundryOrder) -> dict:
    items = list(order.items.select_related('garment_type'))
    units = order_units(order)

    if units:
        scanned = [unit for unit in units if unit.scanned_at]
        return {
            'order_id': order.id,
            'declared_total': len(units),
            'scanned_total': len(scanned),
            'is_complete': len(scanned) == len(units),
            # El detalle por tipo se sigue publicando: es lo que el operador lee
            # para saber QUÉ era la unidad que falta, ya que el adhesivo del
            # pesaje no lo dice.
            'items': _progress_items(items),
            'mode': PACKING_MODE_UNIT,
            'units': [
                {'sequence': unit.sequence, 'code': unit.code, 'is_scanned': unit.scanned_at is not None}
                for unit in units
            ],
        }

    return {
        'order_id': order.id,
        'declared_total': sum(item.quantity for item in items),
        'scanned_total': sum(item.scanned_quantity for item in items),
        'is_complete': all(item.scanned_quantity >= item.quantity for item in items) and bool(items),
        'items': _progress_items(items),
        'mode': PACKING_MODE_TYPE,
        'units': [],
    }


def _progress_items(items) -> list[dict]:
    return [
            {
                'item_id': item.id,
                'garment_type_id': item.garment_type_id,
                # Para prendas fuera de catálogo, el "código" que se pistolea
                # es el nombre digitado (ver `scan_packed_garment`): mostrarlo
                # aquí deja claro qué hay que escribir para resolverla.
                'code': item.garment_type.code if item.garment_type_id else item.custom_name,
                'name': item.display_name,
                'quantity': item.quantity,
                'scanned_quantity': item.scanned_quantity,
            }
            for item in items
    ]


def get_packing_progress(order_id: int) -> dict:
    return _packing_progress(get_order(order_id))


def _item_codes(item: OrderItem) -> set[str]:
    """Códigos con los que se puede pistolear esta prenda, en minúsculas."""
    codes = {item.label_code, item.custom_name}
    if item.garment_type_id:
        codes.add(item.garment_type.code)
    return {code.strip().lower() for code in codes if code and code.strip()}


def _match_item(order: LaundryOrder, code: str) -> OrderItem | None:
    """Resuelve el segmento de prenda de una etiqueta a la línea de la guía.

    Acepta el código de etiqueta lavable (`label_code`) y también el del
    catálogo o el nombre digitado, que son los que traen las guías anteriores a
    las etiquetas por prenda y los que el operador tipea de memoria.
    """
    return (
        order.items.select_related('garment_type')
        .filter(
            Q(label_code__iexact=code) | Q(garment_type__code__iexact=code) | Q(custom_name__iexact=code)
        )
        .first()
    )


def _register_scanned_unit(order: LaundryOrder, code: str, user: User) -> dict:
    """Marca como empacada una prenda física identificada por su adhesivo.

    A diferencia del modo por tipo, aquí no hay cantidad: un adhesivo es una
    prenda. Ese es justamente el punto — pistolear dos veces la misma prenda ya
    no la cuenta dos veces, sino que avisa.
    """
    from weighing.models import WeighLabel

    unit = WeighLabel.objects.select_for_update().filter(weigh_in__order=order, code__iexact=code).first()
    if unit is None:
        raise OrderFlowError(
            f'La etiqueta "{code}" no pertenece al morral '
            f'{order.order_number or order.reference}.'
        )
    if unit.scanned_at is not None:
        raise OrderFlowError(f'La prenda {unit.code} ya se había pistoleado en este morral.')

    unit.scanned_at = timezone.now()
    unit.scanned_by = user
    unit.save(update_fields=['scanned_at', 'scanned_by'])
    if order.status == OrderStatus.RECEIVED:
        _advance_status(order, OrderStatus.QUALITY_CHECK, user, note='Morral abierto por pistoleo de prenda.')
    return _packing_progress(order)


def _resolve_missing_unit(order: LaundryOrder, code: str, user: User) -> dict:
    """La prenda que faltaba apareció y se pistoleó en una guía ya cerrada.

    Equivale a `resolve_missing_item` con resolución ENCONTRADA, pero sobre una
    unidad física en vez de una línea de la guía. Se registra igual en
    `MissingItemResolution` —sin `item`, con el código de la unidad en la nota—
    para que el panel de Calidad siga contando encontradas contra compradas.
    """
    progress = _register_scanned_unit(order, code, user)
    MissingItemResolution.objects.create(
        order=order,
        item=None,
        resolution_type=MissingItemResolution.ResolutionType.FOUND,
        quantity=1,
        resolved_by=user,
        note=code.strip().upper(),
    )
    if progress['is_complete']:
        order.packed_at = timezone.now()
        order.packed_by = user
        order.save(update_fields=['packed_at', 'packed_by', 'updated_at'])
        # Solo desde INCOMPLETA: si el morral ya se despachó, resolver la prenda
        # no lo devuelve a planta, así que conserva DESPACHADA. La completitud
        # recuperada se lee en el progreso y en las resoluciones registradas.
        if order.status == OrderStatus.INCOMPLETE:
            _advance_status(order, OrderStatus.COMPLETED, user, note='Prenda(s) faltante(s) resuelta(s).')
    return progress


def unshipped_resolutions(order: LaundryOrder):
    """Prendas ya resueltas que siguen en planta esperando su envío a faena.

    Son la carga del segundo despacho: el morral salió incompleto, la prenda
    apareció (o se compró para reponerla) y ahora tiene que viajar sola. Ver
    `MissingItemResolution.shipped_at`.
    """
    return order.missing_item_resolutions.filter(shipped_at__isnull=True)


def _is_resolving_missing(order: LaundryOrder) -> bool:
    """¿Un pistoleo de prenda sobre esta guía resuelve un faltante?

    Cierto en INCOMPLETA siempre, y en DESPACHADA solo si el morral salió con
    un faltante que sigue pendiente. La condición sobre INCOMPLETA no mira
    `incomplete_at` a propósito: el legado lo dejó nulo en muchas guías y esas
    igual deben poder resolverse.
    """
    if order.status == OrderStatus.INCOMPLETE:
        return True
    return (
        order.status == OrderStatus.DISPATCHED
        and order.incomplete_at is not None
        and not _packing_progress(order)['is_complete']
    )


def _register_scanned_item(order: LaundryOrder, code: str, quantity: int, user: User) -> dict:
    """Suma una prenda pistoleada a una guía ya resuelta y bloqueada."""
    item = _match_item(order, code)
    if item is None:
        raise OrderFlowError(
            f'La guía {order.order_number or order.reference} no declara ninguna prenda con el código "{code}".'
        )
    if item.scanned_quantity + quantity > item.quantity:
        raise OrderFlowError(
            f'Sobran prendas: la guía declara {item.quantity} de {item.display_name} '
            f'y ya se pistolearon {item.scanned_quantity}.'
        )

    item.scanned_quantity += quantity
    item.save(update_fields=['scanned_quantity'])
    if order.status == OrderStatus.RECEIVED:
        _advance_status(order, OrderStatus.QUALITY_CHECK, user, note='Primer pistoleo de empaque.')
    return _packing_progress(get_order(order.id))


@transaction.atomic
def scan_packed_garment(order_id: int, code: str, quantity: int, user: User) -> dict:
    """Suma una prenda pistoleada al morral limpio (FLUJO_NEGOCIO.md §4, paso 6).

    Qué es `code` depende del modo de la guía (ver `_packing_progress`): en el
    modo por unidad es el adhesivo de una prenda física (`P1375A-03`); en el
    modo por tipo, el código del tipo de prenda del catálogo, o el nombre
    digitado para las que van fuera de catálogo. El primer pistoleo de la guía
    la saca de RECIBIDA: ya no espera un clic manual de "en revisión".

    Exige tener la guía ya resuelta. El pistoleo de una etiqueta lavable, que
    resuelve la guía y la prenda en un solo gesto, va por `scan_packing_code`.
    """
    order = LaundryOrder.objects.select_for_update().get(pk=order_id)
    if order_units(order):
        # Igual que en `scan_packing_code`: sobre una guía ya cerrada
        # incompleta, pistolear la unidad que reaparece la resuelve como
        # Encontrada en vez de sumarla como un pistoleo más.
        if _is_resolving_missing(order):
            return _resolve_missing_unit(order, code.strip(), user)
        return _register_scanned_unit(order, code.strip(), user)
    return _register_scanned_item(order, code.strip(), quantity, user)


@transaction.atomic
def finish_packing(order_id: int, user: User, note: str = '') -> LaundryOrder:
    """Cierra el empaque: completa la guía si el morral quedó completo, si no la marca incompleta.

    Es el segundo touchpoint del almacenamiento online. Al completar se fija
    `packed_at` y se deja la guía lista para imprimir la boleta. Completada e
    Incompleta solo se alcanzan por esta vía —pistoleando—, nunca a mano.
    """
    order = LaundryOrder.objects.select_for_update().select_related('company').get(pk=order_id)
    progress = _packing_progress(order)

    if not progress['is_complete']:
        if progress['mode'] == PACKING_MODE_UNIT:
            # El adhesivo del pesaje no dice qué prenda es, así que se nombra la
            # unidad. El detalle declarado en la guía queda al lado en pantalla
            # para que el operador deduzca qué buscar.
            pendientes = [unit['code'] for unit in progress['units'] if not unit['is_scanned']]
            faltantes = f'faltan {len(pendientes)} prendas: {", ".join(pendientes)}'
        else:
            faltantes = ', '.join(
                f'{item["name"]}: faltan {item["quantity"] - item["scanned_quantity"]}'
                for item in progress['items']
                if item['scanned_quantity'] < item['quantity']
            )
        observation = note or f'Morral incompleto al empacar. {faltantes}'
        order.observations = f'{order.observations}\n{observation}'.strip()
        order.save(update_fields=['observations', 'updated_at'])
        _advance_status(order, OrderStatus.INCOMPLETE, user, note=observation)
        return order

    order.packed_at = timezone.now()
    order.packed_by = user
    order.save(update_fields=['packed_at', 'packed_by', 'updated_at'])
    _advance_status(order, OrderStatus.COMPLETED, user, note=note or 'Morral validado por pistoleo.')
    return order


# --- Paso 7: despacho del morral cerrado ---


@transaction.atomic
def dispatch_order(order_id: int, user: User, note: str = '') -> LaundryOrder:
    """Saca de planta un morral ya cerrado (FLUJO_NEGOCIO.md §4, paso 7).

    Es el tercer disparo de la boleta sobre el mismo morral: el primero lo abre,
    el segundo lo cierra —Completa o Incompleta, según lo pistoleado— y este lo
    despacha. Separar el cierre del despacho es lo que permite distinguir un
    morral listo en el andén de uno que ya viaja, que antes eran el mismo
    COMPLETADA y por eso el estado se leía como "despachada completa".

    Se despacha igual un morral Incompleto: la operación no retiene el envío
    esperando una prenda, esa viaja después (ver `RESOLVABLE_STATUSES`).

    Y por eso es REPETIBLE. Cuando la prenda que faltaba aparece —o se compra
    una para reponerla— con el morral ya despachado, esa prenda tiene que
    viajar en su propio envío, que casi nunca es el mismo camión. Ese segundo
    despacho no mueve el estado (la guía ya está DESPACHADA, y no volvió a
    planta): sella `shipped_at` en las resoluciones que salen, de modo que cada
    envío quede emparejado con su llegada en `confirm_clean_reception`, que ya
    era repetible por el otro lado.
    """
    order = LaundryOrder.objects.select_for_update().select_related('company').get(pk=order_id)
    if order.status not in DISPATCHABLE_STATUSES:
        raise OrderFlowError(
            f'La guía {order.order_number or order.reference} está '
            f'{OrderStatus(order.status).label} y no tiene nada que despachar.'
        )

    pending = list(unshipped_resolutions(order).select_related('item__garment_type'))
    is_first_dispatch = order.status != OrderStatus.DISPATCHED

    if not is_first_dispatch and not pending:
        # Ya se despachó y no hay ninguna prenda esperando: un disparo más
        # sobre la misma boleta no representa ningún envío.
        raise OrderFlowError(
            f'La guía {order.order_number or order.reference} ya está despachada y no tiene '
            'prendas pendientes de enviar.'
        )

    now = timezone.now()
    if pending:
        MissingItemResolution.objects.filter(pk__in=[r.pk for r in pending]).update(
            shipped_at=now, shipped_by=user
        )

    if not is_first_dispatch:
        detalle = ', '.join(r.item.display_name if r.item_id else r.note for r in pending)
        OrderStatusHistory.objects.create(
            order=order, previous_status=order.status, new_status=order.status, changed_by=user,
            note=(note or f'Prenda(s) despachada(s) a faena en envío aparte: {detalle}.')[:255],
        )
        return order

    if not note:
        note = (
            'Morral despachado a faena.'
            if order.status == OrderStatus.COMPLETED
            else 'Morral despachado a faena con prenda(s) faltante(s) pendiente(s).'
        )
    _advance_status(order, OrderStatus.DISPATCHED, user, note=note)
    return order


class AmbiguousReferenceError(Exception):
    """El `ref` pistoleado calza con más de una guía abierta.

    Pasa cuando una guía vieja quedó sin cerrar y su ref se reutilizó en una
    semana posterior. Se traslada la decisión al operador con las guías
    candidatas: reconoce la suya por trabajador y empresa, que es lo que tiene
    a la vista, y no por un dato de calendario que no puede deducir.
    """

    def __init__(self, reference: str, candidates: list[LaundryOrder]):
        self.reference = reference
        self.candidates = candidates
        super().__init__(
            f'El ref "{reference}" corresponde a {len(candidates)} guías todavía en planta: '
            'elige a cuál pertenece la prenda.'
        )


def split_garment_label(code: str) -> tuple[str, str]:
    """Descompone `P1005-TOA` en (ref del morral, código de prenda).

    Devuelve el código intacto y una prenda vacía cuando no trae separador: ese
    es el caso de la boleta del morral, cuyo código se resuelve por otra vía.
    """
    reference, separator, label_code = code.rpartition(GARMENT_LABEL_SEPARATOR)
    if not separator:
        return code, ''
    return reference.strip(), label_code.strip()


def _find_order_in_statuses(
    code: str, statuses: tuple[str, ...], reference_only: bool, missing_detail: str
) -> LaundryOrder:
    """Resuelve un código de planta a la única guía en `statuses` que lo lleva.

    El `ref` no es único en el histórico (el número se reutiliza al cerrar un
    ciclo), pero sí lo es en la práctica entre las guías que todavía están en
    planta, que son las únicas cuyo morral puede estar físicamente ahí.

    `reference_only` distingue las dos etiquetas: la lavable trae el ref del
    morral en ese segmento y nada más, mientras que la boleta se pistoléa
    indistintamente por OT, ref o código de control (igual que
    `find_order_by_code`, que sí mira el histórico completo porque sirve a la
    consulta de faena, donde la guía ya está entregada).
    """
    lookup = (
        Q(reference=code)
        if reference_only
        else Q(order_number=code) | Q(reference=code) | Q(control_code=code)
    )
    candidates = list(
        LaundryOrder.objects.select_related(
            'worker', 'company', 'company__client', 'delivery_room', 'delivery_room__camp', 'weigh_in'
        )
        .prefetch_related('items__garment_type', 'weigh_in__labels')
        .filter(lookup, status__in=statuses)
        .order_by('-received_at')
    )
    if not candidates:
        raise LaundryOrder.DoesNotExist(missing_detail.format(code=code))
    if len(candidates) > 1:
        raise AmbiguousReferenceError(code, candidates)
    return candidates[0]


def find_open_order(code: str, reference_only: bool = False) -> LaundryOrder:
    """Resuelve un código al morral que sigue ABIERTO en la mesa de empaque.

    Ver `OPEN_PACKING_STATUSES`: son los estados en que todavía se pistolean
    prendas o queda un cierre pendiente.
    """
    return _find_order_in_statuses(
        code, OPEN_PACKING_STATUSES, reference_only,
        'No hay ninguna guía abierta con el código "{code}".',
    )


def find_dispatchable_order(code: str) -> LaundryOrder:
    """Resuelve la boleta de una guía que todavía tiene algo que despachar.

    Es la contraparte de `find_open_order` para el paso 7. Cubre los dos
    envíos: el morral cerrado que aún no sale de planta (Completa o
    Incompleta), y la guía ya despachada a la que le quedan prendas resueltas
    esperando su envío aparte.

    Una guía despachada SIN prendas pendientes queda fuera a propósito: ya no
    tiene carga, y su ref puede estar reutilizado por otro morral (ver
    `ReferenceCounter`), así que resolverla sería imputar el pistoleo a la guía
    equivocada. Solo por boleta —el despacho mueve carga, nunca identifica una
    prenda—, por eso no admite `reference_only`.
    """
    missing_detail = 'No hay ninguna carga pendiente de despacho con el código "{code}".'
    order = _find_order_in_statuses(code, DISPATCHABLE_STATUSES, False, missing_detail)
    if order.status == OrderStatus.DISPATCHED and not unshipped_resolutions(order).exists():
        raise LaundryOrder.DoesNotExist(missing_detail.format(code=code))
    return order


def find_resolvable_order(code: str, reference_only: bool = False) -> LaundryOrder:
    """Resuelve el ref de un morral ya despachado que dejó una prenda pendiente.

    Es el único caso en que una guía fuera de planta sigue aceptando pistoleos
    de prenda: salió incompleta y la que faltaba apareció después. Se exige que
    el faltante siga abierto para que el ref de una guía despachada completa
    NO sea resoluble — su número ya puede estar reutilizado por otro morral
    (ver `ReferenceCounter`), y ahí una coincidencia sería una prenda imputada
    a la guía equivocada.
    """
    missing_detail = 'No hay ninguna guía abierta con el código "{code}".'
    order = _find_order_in_statuses(code, (OrderStatus.DISPATCHED,), reference_only, missing_detail)
    if not _is_resolving_missing(order):
        raise LaundryOrder.DoesNotExist(missing_detail.format(code=code))
    return order


def scan_dispatch_code(code: str, user: User, note: str = '') -> LaundryOrder:
    """Pistoleo único del módulo Despacho (FLUJO_NEGOCIO.md §4, paso 7).

    Un solo disparo resuelve la boleta y despacha: acá no hay nada que abrir ni
    cerrar, así que a diferencia de `scan_packing_code` no hace falta deducir
    una acción — si el código resuelve a un morral con algo pendiente de
    despacho, ese mismo pistoleo lo saca de planta (o envía la prenda resuelta
    aparte, si el morral ya había viajado).

    `find_dispatchable_order` es lo que hace esto seguro: restringe a las
    guías vivas para el despacho y reventa `AmbiguousReferenceError` si el
    código calza con más de una, en vez de despachar la que no corresponde —
    el mismo resguardo que ya tenía la mesa de empaque.
    """
    order = find_dispatchable_order(code)
    return dispatch_order(order.id, user=user, note=note)


@transaction.atomic
def scan_packing_code(code: str, user: User, quantity: int = 1) -> dict:
    """Pistoleo único de la mesa de empaque (FLUJO_NEGOCIO.md §4, paso 6).

    El operador dispara siempre al mismo endpoint y el sistema deduce qué hacer
    según lo que trae el código, para que no tenga que elegir modo en pantalla:

    - Boleta del morral (OT, ref o código de control): el primer disparo lo
      abre, el segundo lo cierra. Un tercer disparo sobre un morral ya cerrado
      ya no hace nada aquí — el despacho (`dispatch_order`) es un módulo
      aparte, para que la mesa de empaque no dependa de un cuarto estado
      físico distinto (dejar el morral en el andén vs. sacarlo de planta).
    - Etiqueta lavable de una prenda (`P1005-TOA`): abre el morral si hacía
      falta y marca la prenda en el mismo gesto.

    Cerrar es `finish_packing`, así que el morral queda Completo o Incompleto
    según lo que se alcanzó a pistolear — nunca por una decisión manual.
    """
    code = code.strip()
    if not code:
        raise OrderFlowError('No se recibió ningún código.')

    reference, label_code = split_garment_label(code)
    if label_code:
        try:
            order = find_open_order(reference, reference_only=True)
        except LaundryOrder.DoesNotExist:
            # El morral pudo despacharse incompleto y la prenda aparecer
            # después: ahí el ref sigue siendo pistoleable para resolverla.
            order = find_resolvable_order(reference, reference_only=True)
        # `find_open_order` no bloquea la fila: se vuelve a leer con
        # select_for_update para serializar dos pistoleos simultáneos de la
        # misma guía, que es lo normal con varios operadores en la mesa.
        locked = LaundryOrder.objects.select_for_update().get(pk=order.id)

        if order_units(locked):
            # Modo unidad: el segmento es el número de prenda dentro del morral,
            # no un tipo. Se resuelve contra los adhesivos que emitió la báscula.
            if _is_resolving_missing(locked):
                progress = _resolve_missing_unit(locked, code, user)
                return {
                    'action': PACKING_ACTION_FOUND,
                    'order': get_order(order.id),
                    'progress': progress,
                }
            progress = _register_scanned_unit(locked, code, user)
            return {'action': PACKING_ACTION_SCANNED, 'order': get_order(order.id), 'progress': progress}

        if _is_resolving_missing(locked):
            # El morral ya se cerró y esta prenda es la que faltaba: pistolearla
            # es resolverla como Encontrada, con su registro en
            # `MissingItemResolution`, no sumarla como un pistoleo más.
            item = _match_item(locked, label_code)
            if item is None:
                raise OrderFlowError(
                    f'La guía {locked.order_number or locked.reference} no declara ninguna '
                    f'prenda con el código "{label_code}".'
                )
            resolve_missing_item(
                order.id, item.id, MissingItemResolution.ResolutionType.FOUND,
                quantity, user=user, code=label_code,
            )
            return {
                'action': PACKING_ACTION_FOUND,
                'order': get_order(order.id),
                'progress': get_packing_progress(order.id),
            }

        progress = _register_scanned_item(locked, label_code, quantity, user)
        return {'action': PACKING_ACTION_SCANNED, 'order': get_order(order.id), 'progress': progress}

    try:
        order = find_open_order(code)
    except LaundryOrder.DoesNotExist:
        try:
            # Sigue existiendo y tiene algo pendiente de despacho (cerrado en
            # el andén, o despachado con una prenda resuelta sin enviar), pero
            # eso ya no se resuelve pistoleando en la mesa de empaque.
            order = find_dispatchable_order(code)
        except LaundryOrder.DoesNotExist:
            # Existe pero ya salió de planta: vale la pena decirlo con el estado
            # en vez de un "no encontrado" que haría dudar de la etiqueta.
            order = find_order_by_code(code)
            raise OrderFlowError(
                f'La guía {order.order_number or order.reference} está '
                f'{OrderStatus(order.status).label} y su morral ya no está en planta.'
            ) from None
        raise OrderFlowError(
            f'La guía {order.order_number or order.reference} ya está '
            f'{OrderStatus(order.status).label} y lista para despacho. '
            'Pistoléala en el módulo Despacho, no en la mesa de empaque.'
        ) from None

    locked = LaundryOrder.objects.select_for_update().get(pk=order.id)
    if locked.status == OrderStatus.RECEIVED:
        _advance_status(locked, OrderStatus.QUALITY_CHECK, user, note='Morral abierto por pistoleo de boleta.')
        action = PACKING_ACTION_OPENED
    else:
        # QUALITY_CHECK es el único estado que puede llegar hasta acá: el
        # morral ya estaba abierto, así que este segundo disparo lo cierra.
        # `finish_packing` decide Completa o Incompleta según lo pistoleado.
        finish_packing(order.id, user=user)
        action = PACKING_ACTION_CLOSED
    return {'action': action, 'order': get_order(order.id), 'progress': get_packing_progress(order.id)}


@transaction.atomic
def resolve_missing_item(
    order_id: int,
    item_id: int,
    resolution_type: str,
    quantity: int,
    user: User,
    code: str = '',
    purchase_cost: float | None = None,
    note: str = '',
) -> LaundryOrder:
    """Resuelve `quantity` unidades de una prenda que faltó en una guía INCOMPLETA.

    Una prenda puede resolverse en varias tandas y por distintos caminos: p.ej.
    de 2 faltantes, 1 aparece (ENCONTRADA, se pistolea con su código) y 1 hay
    que reponerla (COMPRADA). `purchase_cost` es el costo POR UNIDAD de la
    reposición; el total de esa tanda es `purchase_cost * quantity`.

    Cuando la última unidad pendiente de toda la guía queda resuelta, el empaque
    se completa igual que si hubiera calzado a la primera —incluido `packed_at`,
    para que la línea de tiempo muestre "Empaquetado" alcanzado—, pero
    conservando `incomplete_at` para poder medir cuánto demoró la resolución.

    Sigue disponible después del despacho: un morral se despacha incompleto y
    la prenda que aparece viaja en un envío aparte, así que el faltante se
    resuelve igual con la guía ya en DESPACHADA (ver `_is_resolving_missing`).
    """
    order = LaundryOrder.objects.select_for_update().select_related('company').get(pk=order_id)
    if not _is_resolving_missing(order):
        raise OrderFlowError(
            'Solo se puede resolver una prenda faltante en una guía Incompleta, '
            'o en una ya despachada que salió con el faltante pendiente.'
        )

    item = order.items.select_related('garment_type').get(pk=item_id)
    missing = item.quantity - item.scanned_quantity
    if quantity > missing:
        raise OrderFlowError(
            f'{item.display_name}: solo faltan {missing}, no se pueden resolver {quantity}.'
        )

    if resolution_type == MissingItemResolution.ResolutionType.FOUND:
        # Se acepta cualquiera de sus códigos: la prenda que reaparece trae
        # pegada su etiqueta lavable, no el código del catálogo.
        if code.strip().lower() not in _item_codes(item):
            raise OrderFlowError(f'El código pistoleado no corresponde a {item.display_name}.')
    elif resolution_type == MissingItemResolution.ResolutionType.PURCHASED:
        if not purchase_cost or purchase_cost <= 0:
            raise OrderFlowError('Debes indicar el costo de la prenda comprada.')
    else:
        raise OrderFlowError(f'Tipo de resolución inválido: "{resolution_type}".')

    item.scanned_quantity += quantity
    item.save(update_fields=['scanned_quantity'])
    MissingItemResolution.objects.create(
        order=order, item=item, resolution_type=resolution_type, quantity=quantity,
        purchase_cost=purchase_cost if resolution_type == MissingItemResolution.ResolutionType.PURCHASED else None,
        resolved_by=user, note=note,
    )

    if _packing_progress(order)['is_complete']:
        order.packed_at = timezone.now()
        order.packed_by = user
        order.save(update_fields=['packed_at', 'packed_by', 'updated_at'])
        # Igual que en `_resolve_missing_unit`: un morral ya despachado no
        # vuelve a COMPLETADA, porque no volvió a planta.
        if order.status == OrderStatus.INCOMPLETE:
            _advance_status(order, OrderStatus.COMPLETED, user, note='Prenda(s) faltante(s) resuelta(s).')
    return get_order(order_id)


# --- Paso 8: recepción del morral limpio en faena ---


@transaction.atomic
def confirm_clean_reception(order_id: int, user: User, note: str = '') -> LaundryOrder:
    """El supervisor en faena abre el morral limpio y confirma que llegó correcto.

    Este hito no existía en Access y es requisito explícito del flujo real
    (FLUJO_NEGOCIO.md §4, paso 8). Es repetible: si la guía se envió incompleta
    y la prenda faltante viaja después en un envío aparte, cada llegada física
    a faena queda registrada por separado (una fila de `SiteScan` por envío).
    """
    order = LaundryOrder.objects.select_for_update().get(pk=order_id)
    # El gate era COMPLETADA/INCOMPLETA solo porque no existía un estado para
    # "salió de planta". Ahora existe, así que se exige el despacho: un morral
    # cerrado pero todavía en el andén no puede haber llegado a faena.
    if order.status not in (OrderStatus.DISPATCHED, OrderStatus.DELIVERED):
        raise OrderFlowError(
            'Solo se puede recibir en faena un morral ya despachado. '
            f'La guía {order.order_number or order.reference} está {OrderStatus(order.status).label}.'
        )

    SiteScan.objects.create(
        # `scanned_code` no admite null; si la guía no tiene OT física se usa
        # el `reference` (siempre existe) como código de respaldo.
        kind=SiteScan.Kind.CLEAN_IN, scanned_code=order.order_number or order.reference, order=order,
        scanned_at=timezone.now(), scanned_by=user, note=note,
    )
    return get_order(order_id)


# --- Paso 9: entrega en habitación ---


@transaction.atomic
def register_delivery(order_id: int, user: User, note: str = '', delivered_at: datetime | None = None) -> LaundryOrder:
    """Confirma una entrega; la evidencia móvil se agrega en el servicio de escaneo."""
    order = LaundryOrder.objects.select_related('company').get(pk=order_id)
    if (
        order.company.delivery_flow == Company.DeliveryFlow.WITH_ROOM_DELIVERY
        and not order.site_scans.filter(kind=SiteScan.Kind.CLEAN_IN).exists()
    ):
        raise OrderFlowError('El morral aún no fue recibido en faena por el supervisor.')

    updated = update_status(order_id, OrderStatus.DELIVERED, user=user, note=note)
    if delivered_at is not None:
        updated.delivered_at = delivered_at
        updated.save(update_fields=['delivered_at', 'updated_at'])
    SiteScan.objects.create(
        kind=SiteScan.Kind.DELIVERY, scanned_code=order.order_number or order.reference, order=order,
        scanned_at=updated.delivered_at, scanned_by=user, note=note,
    )
    return get_order(order_id)


def get_site_counters(date_from: datetime | None = None, date_to: datetime | None = None) -> dict:
    """Contadores ENTREGADOS / DESPACHADOS de la pantalla de faena (FLUJO_NEGOCIO.md §4, paso 2).

    "Despachados" son los morrales que ya salieron de planta: DESPACHADA y
    ENTREGADA. COMPLETADA quedó fuera al reponerse el despacho como estado
    propio — un morral cerrado sobre el andén todavía no viaja, y contarlo como
    despachado era exactamente lo que hacía este contador cuando el concepto no
    tenía estado. "Pendiente de entrega" es DESPACHADA a secas: viajó pero aún
    no se registra su entrega en habitación.
    """
    queryset = LaundryOrder.objects.all()
    if date_from is not None:
        queryset = queryset.filter(received_at__gte=date_from)
    if date_to is not None:
        queryset = queryset.filter(received_at__lte=date_to)

    # Todos los contadores van con `distinct`: `clean_received_at_site` obliga a
    # unir con `site_scans` y esa unión multiplica la fila de una guía con
    # varios pistoleos, inflando de paso a los demás contadores del mismo
    # aggregate (por eso `dispatched` no cuadraba con la suma por estado).
    counters = queryset.aggregate(
        dispatched=Count(
            'id', filter=Q(status__in=[OrderStatus.DISPATCHED, OrderStatus.DELIVERED]), distinct=True
        ),
        delivered=Count('id', filter=Q(delivered_at__isnull=False), distinct=True),
        clean_received_at_site=Count(
            'id', filter=Q(site_scans__kind=SiteScan.Kind.CLEAN_IN), distinct=True
        ),
        pending_delivery=Count('id', filter=Q(status=OrderStatus.DISPATCHED), distinct=True),
    )
    counters['by_status'] = {
        row['status']: row['total']
        for row in queryset.values('status').annotate(total=Count('id')).order_by('status')
    }
    return counters


def resolve_order_faena_name(order: LaundryOrder) -> str:
    """Faena de la guía, tal como se imprime en la etiqueta lavable y la boleta.

    Manda la faena configurada en el cliente (`companies.Client.faena`): es la
    que declara el contrato y existe siempre, incluso en Flujo 2, donde no hay
    habitación de destino de la que deducirla.

    Si el cliente todavía no la tiene configurada se cae a la faena del
    campamento al que va el morral, que es el mismo lugar físico visto desde el
    otro lado. Vacío solo si no hay ni una ni otra.

    Requiere `select_related('company__client__faena', 'delivery_room__camp__faena')`.
    """
    client_faena = order.company.client.faena
    if client_faena is not None:
        return client_faena.name
    if order.delivery_room_id:
        return order.delivery_room.camp.faena.name
    return ''


def build_receipt(order_id: int) -> dict:
    """Datos de la boleta impresa que acompaña la ropa limpia de vuelta a faena.

    Replica los campos del comprobante real descrito en FLUJO_NEGOCIO.md §3.2.
    El código QR lleva el RUT sin puntos ni guion, tal como el impreso actual.
    """
    order = get_order(order_id)
    worker = order.worker
    return {
        'order_number': order.order_number,
        'reference': order.reference,
        'control_code': order.control_code,
        'ticket_number': order.ticket_number,
        'company_name': order.company.name,
        'company_logo_url': build_object_url(order.company.logo_key),
        # Faena y condición de contratista: la boleta viaja de vuelta con el
        # morral, así que en faena se lee de un vistazo a quién pertenece la
        # ropa y si es del mandante o de una contratista.
        'faena': resolve_order_faena_name(order),
        'is_contractor': order.company.is_contractor,
        'worker_name': worker.full_name,
        # El comprobante físico trae una casilla de teléfono junto al n° de OT.
        # Casi siempre viene vacía o en "0" (el trabajador no lo anota), pero la
        # casilla se imprime igual para no alterar el formato conocido.
        'phone': worker.phone,
        'national_id': worker.national_id,
        # La boleta muestra el destino DE ESTA GUÍA, no dónde vive hoy el
        # trabajador: si se mudó mientras su ropa estaba en planta, el morral
        # igual se entrega donde correspondía al recibirlo.
        'camp': order.delivery_room.camp.name if order.delivery_room_id else '',
        'room': order.delivery_room.number if order.delivery_room_id else '',
        'shift': order.shift or worker.shift,
        'weight_kg': float(order.weight_kg) if order.weight_kg is not None else None,
        'garment_count': order.garment_count,
        'promised_at': order.promised_at,
        'qr_payload': re.sub(r'[.\-]', '', worker.national_id),
        'items': [
            {'name': item.display_name, 'quantity': item.quantity} for item in order.items.all()
        ],
    }


def build_garment_labels(order_id: int) -> list[dict]:
    """Etiquetas lavables a imprimir, una por línea declarada en la guía.

    Se emiten al digitalizar la OT, que es el primer momento en que existen las
    líneas de la guía y por tanto su código de etiqueta. Se devuelve una sola
    etiqueta por línea aunque declare varias unidades (ej. 4 poleras): las
    unidades de una misma prenda comparten físicamente el mismo código, así que
    un solo adhesivo alcanza y se pistolea tantas veces como unidades vuelvan
    del lavado (`scanned_quantity`), sin importar cuál volvió primero.

    Sigue la misma convención que `build_receipt`: el backend entrega los datos
    y quien imprime arma el layout físico.
    """
    order = get_order(order_id)
    faena = resolve_order_faena_name(order)
    is_contractor = order.company.is_contractor
    labels = []
    for item in order.items.all():
        # Las guías anteriores a las etiquetas por prenda no tienen
        # `label_code`; se cae al código de catálogo, que es lo que igual
        # reconoce `_match_item` al pistolear.
        label_code = item.label_code or (item.garment_type.code if item.garment_type_id else item.custom_name)
        labels.append({
            'order_id': order.id,
            'order_number': order.order_number,
            'reference': order.reference,
            'label_code': label_code,
            # Lo que lee la pistola: resuelve morral y prenda de una vez.
            'scan_payload': f'{order.reference}{GARMENT_LABEL_SEPARATOR}{label_code}',
            'garment_name': item.display_name,
            'worker_name': order.worker.full_name,
            'company_name': order.company.name,
            # La etiqueta se imprime "FAENA · EMPRESA" y, si la empresa es
            # contratista, "FAENA · EMPRESA · CONTRATISTA": lo que ubica a la
            # prenda suelta en la mesa es la faena, no el campamento.
            'faena': faena,
            'is_contractor': is_contractor,
            'camp': order.delivery_room.camp.name if order.delivery_room_id else '',
            'quantity': item.quantity,
        })
    return labels


def request_photo_upload(order_id: int, filename: str, content_type: str) -> dict:
    get_order(order_id)  # valida existencia
    return build_presigned_upload(folder='guias/fotos', filename=filename, content_type=content_type)


def confirm_photo_upload(order_id: int, object_key: str) -> LaundryOrder:
    order = get_order(order_id)
    order.photo_key = object_key
    order.save(update_fields=['photo_key', 'updated_at'])
    return order


# --- Sincronización offline-first (app móvil) ---


@transaction.atomic
def sync_order(data: OrderSyncIn) -> tuple[LaundryOrder, str]:
    """Upsert idempotente por `client_uuid` para la sincronización en lote de la app móvil.

    Resolución de conflictos: si el servidor tiene una versión más reciente
    que la que trae el dispositivo, se descarta el cambio entrante y se le
    informa al cliente para que vuelva a sincronizar (last-write-wins por
    `updated_at`, no por orden de llegada). El descarte no es silencioso: queda
    en `SyncConflict` para que un supervisor pueda revisarlo.
    """
    worker = Worker.objects.select_related('company').get(pk=data.worker_id)

    try:
        order = LaundryOrder.objects.select_for_update().get(client_uuid=data.client_uuid)
    except LaundryOrder.DoesNotExist:
        shift = data.shift or worker.shift
        order = LaundryOrder.objects.create(
            client_uuid=data.client_uuid,
            order_number=normalize_order_number(data.order_number),
            ticket_number=data.ticket_number,
            worker=worker,
            company=worker.company,
            shift=shift,
            status=data.status,
            weight_kg=data.weight_kg,
            received_at=data.received_at,
            promised_at=data.promised_at or calculate_promised_at(shift, data.received_at),
            delivered_at=data.delivered_at,
            observations=data.observations,
            # El dispositivo no propone ref (ver OrderSyncIn): lo emite el
            # contador al llegar el lote, que es el unico momento en que hay
            # servidor para bloquearlo.
            reference=generate_reference(worker.company.client),
            control_code=data.control_code,
        )
        order.garment_count = _build_items(order, data.items)
        order.save(update_fields=['garment_count'])
        _attach_pending_site_scans(order)
        return order, 'created'

    if data.updated_at <= order.updated_at:
        SyncConflict.objects.create(
            order=order,
            client_uuid=data.client_uuid,
            device_updated_at=data.updated_at,
            server_updated_at=order.updated_at,
            discarded_payload=data.dict(mode='json'),
        )
        return order, 'conflict_skipped'

    order.ticket_number = data.ticket_number
    order.shift = data.shift or order.shift
    order.status = data.status
    order.weight_kg = data.weight_kg
    order.received_at = data.received_at
    order.promised_at = data.promised_at or order.promised_at
    order.observations = data.observations
    # El ref no se toca al sincronizar: se emitio al crear la guia y desde ese
    # momento viaja impreso en los adhesivos pegados a la ropa. Un dispositivo
    # que reenvia el lote no puede reescribirlo sin dejar el morral con dos
    # identidades, una en el papel y otra en la base.
    order.control_code = data.control_code or order.control_code
    if data.delivered_at is not None:
        order.delivered_at = data.delivered_at
    order.items.all().delete()
    order.garment_count = _build_items(order, data.items)
    order.save()
    return order, 'updated'


def sync_batch(orders: list[OrderSyncIn]) -> list[tuple[LaundryOrder, str]]:
    return [sync_order(data) for data in orders]


def list_sync_conflicts(resolved: bool | None = None) -> QuerySet[SyncConflict]:
    queryset = SyncConflict.objects.select_related('order', 'order__worker', 'resolved_by')
    if resolved is True:
        queryset = queryset.filter(resolved_at__isnull=False)
    if resolved is False:
        queryset = queryset.filter(resolved_at__isnull=True)
    # Orden determinístico: LIMIT/OFFSET sin order_by no garantiza páginas
    # estables entre requests.
    return queryset.order_by('-created_at')


def resolve_sync_conflict(conflict_id: int, user: User, note: str = '') -> SyncConflict:
    conflict = SyncConflict.objects.select_related('order').get(pk=conflict_id)
    conflict.resolved_at = timezone.now()
    conflict.resolved_by = user
    conflict.resolution_note = note
    conflict.save(update_fields=['resolved_at', 'resolved_by', 'resolution_note'])
    return conflict


# --- Paso 9 (app móvil): entrega en habitación por doble escaneo QR ---


class DeliveryRoomMismatch(Exception):
    """El QR de la puerta escaneada no es el destino registrado en la guía.

    Lleva ambas habitaciones para que la app pueda mostrar "esperaba A, leíste
    B" y ofrecer confirmar la entrega en la pieza real.
    """

    def __init__(self, message: str, scanned: Room, expected: Room | None):
        super().__init__(message)
        self.scanned = scanned
        self.expected = expected


@transaction.atomic
def confirm_delivery_by_scan(
    client_uuid: UUID,
    order_code: str,
    room_qr: UUID | None,
    user: User,
    latitude: float,
    longitude: float,
    accuracy_meters: float,
    note: str = '',
    delivered_at: datetime | None = None,
    confirm_different_room: bool = False,
) -> dict:
    """Registra una entrega móvil idempotente con evidencia geográfica.

    En Flujo 1 valida la puerta y exige confirmación explícita si no coincide.
    En Flujo 2 no hay puerta: el único escaneo identifica el morral entregado
    directamente al cliente. `client_uuid` hace seguro todo reintento offline.

    Ante una discrepancia lanza `DeliveryRoomMismatch` en vez de entregar a
    ciegas: en faena las piezas se reasignan seguido y dejar la ropa en la
    puerta equivocada es justamente lo que este doble escaneo viene a evitar.
    Si el operador confirma que la pieza real es otra, se registra la entrega
    ahí y la discrepancia queda escrita en la nota del pistoleo.
    """
    def result_from_scan(scan: SiteScan) -> dict:
        expected_room = scan.order.delivery_room
        room_delivery = scan.order.company.delivery_flow == Company.DeliveryFlow.WITH_ROOM_DELIVERY
        return {
            'client_uuid': scan.client_uuid,
            'order': get_order(scan.order_id),
            'delivery_target': 'HABITACION' if room_delivery else 'CLIENTE',
            'scanned_room': scan.room,
            'expected_room': expected_room if room_delivery else None,
            'room_matched': (
                expected_room is not None and expected_room.id == scan.room_id
                if room_delivery else None
            ),
            'latitude': scan.latitude,
            'longitude': scan.longitude,
            'accuracy_meters': scan.accuracy_meters,
            'delivered_at': scan.scanned_at,
        }

    existing = (
        SiteScan.objects.select_related(
            'order', 'order__company', 'order__delivery_room', 'order__delivery_room__camp', 'room', 'room__camp'
        )
        .filter(kind=SiteScan.Kind.DELIVERY, client_uuid=client_uuid)
        .first()
    )
    if existing is not None:
        return result_from_scan(existing)

    found = find_order_by_code(order_code)
    order = LaundryOrder.objects.select_for_update().get(pk=found.id)
    # Dos reintentos simultáneos pueden haber pasado el primer lookup antes de
    # que uno confirme la transacción. Bajo el bloqueo se vuelve a comprobar.
    existing = (
        SiteScan.objects.select_related(
            'order', 'order__company', 'order__delivery_room', 'order__delivery_room__camp', 'room', 'room__camp'
        )
        .filter(kind=SiteScan.Kind.DELIVERY, client_uuid=client_uuid)
        .first()
    )
    if existing is not None:
        return result_from_scan(existing)

    is_room_delivery = order.company.delivery_flow == Company.DeliveryFlow.WITH_ROOM_DELIVERY
    if is_room_delivery and room_qr is None:
        raise OrderFlowError('Este morral corresponde a Flujo 1: debes escanear el QR de la habitación.')

    room = camps_services.get_room_by_qr(room_qr) if room_qr is not None else None
    expected = order.delivery_room if is_room_delivery else None

    room_matched = expected is not None and room is not None and expected.id == room.id
    if is_room_delivery and not room_matched and not confirm_different_room:
        detail = (
            f'La guía se debía entregar en {expected.camp.name} · {expected.number}, '
            f'pero se escaneó {room.camp.name} · {room.number}.'
            if expected is not None
            else 'La guía no tiene habitación de entrega registrada; confirma la pieza escaneada.'
        )
        raise DeliveryRoomMismatch(detail, scanned=room, expected=expected)

    full_note = note
    if is_room_delivery and not room_matched:
        discrepancy = (
            f'Entregada en {room.camp.name} · {room.number} '
            f'(destino registrado: '
            f'{f"{expected.camp.name} · {expected.number}" if expected else "sin registrar"}).'
        )
        full_note = f'{note} {discrepancy}'.strip()[:255]

    updated = register_delivery(order.id, user=user, note=full_note, delivered_at=delivered_at)

    # `register_delivery` ya creó el pistoleo de ENTREGA; se le adjunta la
    # habitación escaneada, que es la evidencia de dónde quedó el morral.
    scan = (
        SiteScan.objects.filter(order_id=order.id, kind=SiteScan.Kind.DELIVERY)
        .order_by('-scanned_at')
        .first()
    )
    if scan is not None:
        scan.client_uuid = client_uuid
        scan.room = room
        scan.latitude = Decimal(str(latitude))
        scan.longitude = Decimal(str(longitude))
        scan.accuracy_meters = accuracy_meters
        scan.save(update_fields=['client_uuid', 'room', 'latitude', 'longitude', 'accuracy_meters'])

    return {
        'client_uuid': client_uuid,
        'order': updated,
        'delivery_target': 'HABITACION' if is_room_delivery else 'CLIENTE',
        'scanned_room': room,
        'expected_room': expected,
        'room_matched': room_matched if is_room_delivery else None,
        'latitude': latitude,
        'longitude': longitude,
        'accuracy_meters': accuracy_meters,
        'delivered_at': updated.delivered_at,
    }
