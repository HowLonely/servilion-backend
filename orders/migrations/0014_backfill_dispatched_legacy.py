from django.db import migrations


def backfill_dispatched(apps, schema_editor):
    """Devuelve a DESPACHADA el archivo legado que la migración 0003 aplanó a COMPLETADA.

    Al reponerse DESPACHADA como estado propio (ver OrderStatus), COMPLETADA
    dejó de significar "producida y ya fuera de planta" y pasó a significar
    "morral cerrado, todavía en el andén". Sin este backfill las ~210k guías
    del import de Access quedarían diciendo que hay 210k morrales esperando
    despacho en la planta, y el contador DESPACHADOS de la app de faena —que
    hoy suma COMPLETADA + ENTREGADA— se desplomaría a un tercio.

    El corte es `packed_at`: solo lo escriben `finish_packing` y
    `resolve_missing_item`, nunca el importador legado, así que separa con
    precisión las guías que este sistema empacó (siguen COMPLETADA, esperando
    su pistoleo de despacho) del archivo importado, que ya viajó.

    No se puede reconstruir cuáles de esas filas eran DESPACHADO y cuáles
    COMPLETO en Access: la 0003 hizo el UPDATE en bloque sin dejar registro. Se
    resuelven todas como DESPACHADA porque es lo cierto de un morral archivado
    hace años, mientras que "esperando despacho" no lo es de ninguno.
    """
    LaundryOrder = apps.get_model('orders', 'LaundryOrder')
    LaundryOrder.objects.filter(status='COMPLETADA', packed_at__isnull=True).update(status='DESPACHADA')


def unbackfill_dispatched(apps, schema_editor):
    """Vuelve a aplanar sobre COMPLETADA lo que no llegó a despacharse por pistoleo.

    Las guías despachadas por este sistema sí tienen `packed_at` (se empacaron
    antes), así que quedan fuera y conservan su despacho real.
    """
    LaundryOrder = apps.get_model('orders', 'LaundryOrder')
    LaundryOrder.objects.filter(status='DESPACHADA', packed_at__isnull=True).update(status='COMPLETADA')


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0013_laundryorder_dispatched_at_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill_dispatched, unbackfill_dispatched),
    ]
