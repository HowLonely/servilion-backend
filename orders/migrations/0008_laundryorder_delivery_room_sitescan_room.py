import django.db.models.deletion
from django.db import migrations, models
from django.db.models import OuterRef, Subquery


def backfill_delivery_room(apps, schema_editor):
    """Rellena el destino de entrega de las guías anteriores a la normalización.

    Solo aplica a las guías que todavía no se entregaron: para una guía ya
    ENTREGADA o COBRADA, la habitación actual del trabajador puede no ser la
    que correspondía en su momento, y escribirla ahí falsearía el histórico.
    Esas guías se dejan en null, que es la respuesta honesta: no se sabe.
    """
    LaundryOrder = apps.get_model('orders', 'LaundryOrder')
    Worker = apps.get_model('workers', 'Worker')

    # Subquery y no F('worker__current_room_id'): Postgres no admite
    # referencias con join en el SET de un UPDATE.
    LaundryOrder.objects.filter(
        delivery_room__isnull=True,
        worker__current_room__isnull=False,
        status__in=['RECIBIDA', 'EN_REVISION', 'INCOMPLETA', 'COMPLETADA'],
    ).update(
        delivery_room_id=Subquery(
            Worker.objects.filter(pk=OuterRef('worker_id')).values('current_room_id')[:1]
        )
    )


def clear_delivery_room(apps, schema_editor):
    """No-op: el campo se elimina al revertir el AddField."""


class Migration(migrations.Migration):

    dependencies = [
        ('camps', '0001_initial'),
        ('orders', '0007_alter_laundryorder_order_number'),
        # El backfill lee `worker.current_room`, que recién existe (y queda
        # poblado) tras la normalización de campamentos.
        ('workers', '0002_worker_current_room'),
    ]

    operations = [
        migrations.AddField(
            model_name='laundryorder',
            name='delivery_room',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='deliveries',
                to='camps.room',
            ),
        ),
        migrations.AddField(
            model_name='sitescan',
            name='room',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='scans',
                to='camps.room',
            ),
        ),
        migrations.RunPython(backfill_delivery_room, clear_delivery_room),
    ]
