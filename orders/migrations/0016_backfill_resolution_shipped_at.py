from django.db import migrations
from django.db.models import F


def backfill_shipped_at(apps, schema_editor):
    """Marca como ya enviadas las prendas resueltas de guías que salieron de planta.

    `shipped_at` nace nulo, y nulo significa "resuelta pero todavía en planta,
    esperando su envío aparte" — la cola de trabajo del segundo despacho (ver
    MissingItemResolution.shipped_at). Sin este backfill toda resolución
    histórica parecería carga pendiente, y la boleta de una guía ya entregada
    volvería a ser pistoleable como si tuviera algo que despachar.

    El corte es el estado de la guía: si ya está DESPACHADA o ENTREGADA, la
    prenda viajó con ella o en el envío que el sistema no registraba, así que
    se sella con su propio `resolved_at` en vez de inventar una fecha. Las
    resoluciones de guías todavía en planta se dejan nulas a propósito: esas
    prendas están ahí de verdad y saldrán con el despacho del morral.
    """
    MissingItemResolution = apps.get_model('orders', 'MissingItemResolution')
    MissingItemResolution.objects.filter(
        shipped_at__isnull=True,
        order__status__in=('DESPACHADA', 'ENTREGADA'),
    ).update(shipped_at=F('resolved_at'))


def unbackfill_shipped_at(apps, schema_editor):
    """Devuelve a nulo lo que este backfill selló (shipped_at == resolved_at)."""
    MissingItemResolution = apps.get_model('orders', 'MissingItemResolution')
    MissingItemResolution.objects.filter(
        shipped_at=F('resolved_at'),
        order__status__in=('DESPACHADA', 'ENTREGADA'),
    ).update(shipped_at=None, shipped_by=None)


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0015_missingitemresolution_shipped_at_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill_shipped_at, unbackfill_shipped_at),
    ]
