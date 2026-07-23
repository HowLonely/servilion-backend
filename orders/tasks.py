from celery import shared_task
from django.db.models import Count, Sum

from companies.models import Client, Company
from orders.models import LaundryOrder, OrderStatus


@shared_task
def generate_billing_report_task(
    date_from: str, date_to: str, company_id: int | None = None, client_id: int | None = None
) -> dict:
    """Agrega guías cobradas por rango de fechas, por empresa o por cliente.

    Se ejecuta en un worker de Celery porque recorrer/agregar miles de guías
    (tabla histórica con cientos de miles de registros) es demasiado pesado
    para responder dentro del ciclo de una request HTTP.

    El monto sale de `billed_amount`, que se congeló al cobrar cada guía leyendo
    el catálogo de precios del cliente en ese momento (ver
    `orders.services.compute_billed_amount`). Si se filtra por `client_id`, el
    reporte agrega todas las empresas del cliente y entrega el desglose por
    empresa; si se filtra por `company_id`, cubre solo esa empresa. El caso
    cliente=empresa (1:1) da el mismo total por ambos caminos.
    """
    orders = LaundryOrder.objects.filter(
        status=OrderStatus.BILLED,
        billed_at__range=(date_from, date_to),
    )

    scope: dict = {}
    if client_id is not None:
        orders = orders.filter(company__client_id=client_id)
        client = Client.objects.get(pk=client_id)
        scope = {'client_id': client_id, 'client_name': client.name}
    elif company_id is not None:
        orders = orders.filter(company_id=company_id)
        company = Company.objects.select_related('client').get(pk=company_id)
        scope = {
            'company_id': company_id,
            'company_name': company.name,
            'client_id': company.client_id,
            'client_name': company.client.name,
        }

    totals = orders.aggregate(
        order_count=Count('id'),
        total_weight_kg=Sum('weight_kg'),
        total_garments=Sum('garment_count'),
        total_amount=Sum('billed_amount'),
    )

    by_company = list(
        orders.values('company_id', 'company__name')
        .annotate(order_count=Count('id'), total_garments=Sum('garment_count'), total_amount=Sum('billed_amount'))
        .order_by('company__name')
    )
    by_worker = list(
        orders.values('worker_id', 'worker__full_name')
        .annotate(order_count=Count('id'), total_garments=Sum('garment_count'))
        .order_by('worker__full_name')
    )

    return {
        **scope,
        'date_from': date_from,
        'date_to': date_to,
        'order_count': totals['order_count'],
        'total_weight_kg': float(totals['total_weight_kg'] or 0),
        'total_garments': totals['total_garments'] or 0,
        'total_amount': float(totals['total_amount'] or 0),
        'by_company': by_company,
        'by_worker': by_worker,
    }
