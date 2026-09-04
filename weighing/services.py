from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from authentication.models import User
from companies.models import Client, Company
from orders.services import GARMENT_LABEL_SEPARATOR, generate_reference

from .models import WeighIn, WeighLabel


class WeighInError(Exception):
    """Regla de negocio del pesaje violada (se traduce a 400 en la API)."""


# Ancho mínimo del número de prenda dentro del morral: `P1375A-03`. Dos dígitos
# porque un morral típico trae menos de veinte prendas y `03` se lee mejor que
# `3` en un adhesivo pequeño; si alguna vez pasa de 99, el formato crece solo a
# tres dígitos sin romper el escaneo (el código se guarda materializado).
SEQUENCE_WIDTH = 2

# Tope de cordura sobre lo que un morral puede traer. No es una regla del
# negocio sino un cortafuegos contra el error de tipeo en una pantalla táctil:
# tocar un cero de más imprime 120 adhesivos y consume papel de verdad.
MAX_GARMENT_COUNT = 99
MAX_WEIGHT_KG = 200


def label_code(reference: str, sequence: int) -> str:
    return f'{reference}{GARMENT_LABEL_SEPARATOR}{sequence:0{SEQUENCE_WIDTH}d}'


def _base_queryset() -> QuerySet[WeighIn]:
    return WeighIn.objects.select_related(
        'client', 'client__faena', 'company', 'weighed_by', 'order'
    ).prefetch_related('labels')


def get_weigh_in(weigh_in_id: int) -> WeighIn:
    return _base_queryset().get(pk=weigh_in_id)


def list_weigh_ins(
    status: str | None = None,
    weighed_by_id: int | None = None,
    since=None,
    limit: int = 50,
) -> list[WeighIn]:
    """Listado corto y reciente: la báscula solo mira lo que acaba de pesar.

    No se pagina porque las dos vistas que lo consumen son acotadas por
    naturaleza —"los pesajes de mi turno" y "los pendientes de digitalizar"— y
    una tabla infinita en una pantalla táctil no se navega.
    """
    queryset = _base_queryset()
    if status:
        queryset = queryset.filter(status=status)
    if weighed_by_id:
        queryset = queryset.filter(weighed_by_id=weighed_by_id)
    if since:
        queryset = queryset.filter(weighed_at__gte=since)
    return list(queryset[:limit])


def find_pending_by_reference(reference: str) -> WeighIn:
    """Resuelve el ref del ticket maestro al pesaje que la digitación va a consumir.

    Solo devuelve pendientes: un ref ya digitalizado o anulado no debe poder
    volver a entrar como origen de una guía nueva. Acepta el código completo de
    un adhesivo de prenda (`P1375A-03`) además del ref pelado, porque en la mesa
    de digitación el ticket maestro se traspapela y lo que queda a mano es
    cualquiera de los adhesivos.
    """
    code = reference.strip().upper()
    reference_part, separator, _ = code.rpartition(GARMENT_LABEL_SEPARATOR)
    if separator:
        code = reference_part.strip()

    try:
        return _base_queryset().get(reference=code, status=WeighIn.Status.PENDING)
    except WeighIn.DoesNotExist:
        raise WeighInError(
            f'No hay ningún pesaje pendiente con el ref "{code}". '
            'Revisa el ticket, o digitaliza la guía sin pesaje previo.'
        ) from None


@transaction.atomic
def create_weigh_in(
    client_id: int,
    company_id: int,
    garment_count: int,
    weight_kg: float,
    weighed_by: User,
) -> WeighIn:
    """Registra el pesaje y emite sus etiquetas (FLUJO_NEGOCIO.md §4, paso 4).

    Todo ocurre en una transacción porque el ref, el pesaje y los adhesivos son
    un solo hecho físico: si algo falla después de consumir el correlativo, se
    revierte y ese ref queda disponible en vez de quemado.
    """
    if garment_count < 1:
        raise WeighInError('El morral tiene que traer al menos una prenda.')
    if garment_count > MAX_GARMENT_COUNT:
        raise WeighInError(
            f'{garment_count} prendas en un morral no es plausible '
            f'(el máximo es {MAX_GARMENT_COUNT}). Revisa el número tecleado.'
        )
    if weight_kg <= 0:
        raise WeighInError('El peso tiene que ser mayor que cero.')
    if weight_kg > MAX_WEIGHT_KG:
        raise WeighInError(
            f'{weight_kg} kg no es plausible para un morral (el máximo es {MAX_WEIGHT_KG} kg).'
        )

    company = Company.objects.select_related('client', 'client__faena').get(pk=company_id)
    client = Client.objects.select_related('faena').get(pk=client_id)
    if company.client_id != client.id:
        # El operador elige cliente y después empresa dentro de ese cliente, así
        # que un cruce aquí significa que la pantalla mandó algo inconsistente.
        raise WeighInError(f'La empresa {company.name} no pertenece al cliente {client.name}.')

    weigh_in = WeighIn.objects.create(
        reference=generate_reference(client),
        client=client,
        company=company,
        garment_count=garment_count,
        weight_kg=weight_kg,
        weighed_at=timezone.now(),
        weighed_by=weighed_by,
    )
    WeighLabel.objects.bulk_create(
        WeighLabel(
            weigh_in=weigh_in,
            sequence=sequence,
            code=label_code(weigh_in.reference, sequence),
        )
        for sequence in range(1, garment_count + 1)
    )
    return get_weigh_in(weigh_in.id)


@transaction.atomic
def void_weigh_in(weigh_in_id: int, user: User, reason: str = '') -> WeighIn:
    """Anula un pesaje mal hecho, mientras nadie lo haya digitalizado.

    No se borra: el morral se pesó de verdad y sus adhesivos pueden andar
    pegados a la ropa. Dejarlo anulado y visible es lo que permite que, si uno
    de esos códigos aparece en la mesa de empaque, el sistema pueda decir "esta
    etiqueta se anuló" en vez de "no existe".

    El ref anulado no se reutiliza: el correlativo ya avanzó y volver atrás
    haría que dos morrales vivos compartieran código.
    """
    weigh_in = WeighIn.objects.select_for_update().get(pk=weigh_in_id)
    if weigh_in.status == WeighIn.Status.VOIDED:
        raise WeighInError(f'El pesaje {weigh_in.reference} ya estaba anulado.')
    if weigh_in.status == WeighIn.Status.DIGITIZED:
        raise WeighInError(
            f'El pesaje {weigh_in.reference} ya se digitalizó en una guía; '
            'corrige el peso o las prendas desde la guía, no desde la báscula.'
        )

    weigh_in.status = WeighIn.Status.VOIDED
    weigh_in.voided_at = timezone.now()
    weigh_in.voided_by = user
    weigh_in.void_reason = reason
    weigh_in.save(update_fields=['status', 'voided_at', 'voided_by', 'void_reason'])
    return get_weigh_in(weigh_in.id)


@transaction.atomic
def consume(weigh_in_id: int, order) -> WeighIn:
    """Enlaza el pesaje a la guía que acaba de digitalizarse.

    Lo llama `orders.services.create_order`. Bloquea la fila para que dos
    digitadores que tipean el mismo ref al mismo tiempo no creen dos guías
    sobre el mismo morral: el segundo se encuentra el pesaje ya digitalizado y
    recibe el error.
    """
    weigh_in = WeighIn.objects.select_for_update().get(pk=weigh_in_id)
    if weigh_in.status == WeighIn.Status.VOIDED:
        raise WeighInError(f'El pesaje {weigh_in.reference} está anulado y no puede digitalizarse.')
    if weigh_in.status == WeighIn.Status.DIGITIZED:
        raise WeighInError(
            f'El pesaje {weigh_in.reference} ya se digitalizó en la guía '
            f'{weigh_in.order.order_number or weigh_in.order.reference}.'
        )

    weigh_in.status = WeighIn.Status.DIGITIZED
    weigh_in.order = order
    weigh_in.digitized_at = timezone.now()
    weigh_in.save(update_fields=['status', 'order', 'digitized_at'])
    return weigh_in


def build_print_job(weigh_in_id: int) -> dict:
    """Datos de impresión del pesaje: los adhesivos y lo que va en el ticket maestro.

    Sigue la convención de `orders.services.build_garment_labels`: el backend
    entrega los datos y quien imprime arma el layout. Acá el que imprime es el
    proceso main de la terminal, que genera el ZPL y lo manda al puerto de la
    etiquetera.
    """
    weigh_in = get_weigh_in(weigh_in_id)
    return {
        'reference': weigh_in.reference,
        'client_name': weigh_in.client.name,
        'company_name': weigh_in.company.name,
        'faena': weigh_in.client.faena.name if weigh_in.client.faena_id else '',
        'is_contractor': weigh_in.company.is_contractor,
        'garment_count': weigh_in.garment_count,
        'weight_kg': float(weigh_in.weight_kg),
        'weighed_at': weigh_in.weighed_at,
        'weighed_by_name': (
            weigh_in.weighed_by.get_full_name() or weigh_in.weighed_by.username
            if weigh_in.weighed_by_id
            else ''
        ),
        'labels': list(weigh_in.labels.all()),
    }
