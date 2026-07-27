import django.db.models.deletion
from django.db import migrations, models


def migrate_camp_room_to_room(apps, schema_editor):
    """Normaliza los antiguos `camp`/`room` de texto libre a Camp/Room.

    El histórico legado ronda las 280.000 guías con estos campos escritos a
    mano, así que borrar las columnas sin traspasarlas perdería el único dato
    de dónde vive cada trabajador. Se crea un Camp por (cliente, nombre) y un
    Room por (camp, número), reutilizando los ya existentes.

    El campamento cuelga del Client del trabajador (vía su empresa): dos
    empresas del mismo cliente que escribieron el mismo campamento comparten la
    fila, que es justamente el punto de normalizarlo.
    """
    Worker = apps.get_model('workers', 'Worker')
    Camp = apps.get_model('camps', 'Camp')
    Room = apps.get_model('camps', 'Room')

    camps: dict[tuple[int, str], int] = {}
    rooms: dict[tuple[int, str], int] = {}

    queryset = Worker.objects.exclude(camp='', room='').select_related('company')
    for worker in queryset.iterator(chunk_size=2000):
        camp_name = (worker.camp or '').strip()
        room_number = (worker.room or '').strip()
        # Sin campamento no hay puerta que etiquetar con un QR.
        if not camp_name:
            continue

        client_id = worker.company.client_id
        camp_key = (client_id, camp_name)
        if camp_key not in camps:
            camp, _ = Camp.objects.get_or_create(
                client_id=client_id, name=camp_name, defaults={'is_active': True}
            )
            camps[camp_key] = camp.id

        # El campamento se crea igual (sirve para el resto de la faena), pero
        # sin número de pieza el trabajador queda sin habitación asignada hasta
        # que alguien la registre.
        if not room_number:
            continue

        room_key = (camps[camp_key], room_number)
        if room_key not in rooms:
            room, _ = Room.objects.get_or_create(
                camp_id=camps[camp_key], number=room_number, defaults={'is_active': True}
            )
            rooms[room_key] = room.id

        Worker.objects.filter(pk=worker.pk).update(current_room_id=rooms[room_key])


def restore_camp_room(apps, schema_editor):
    """Devuelve el nombre del campamento y el número de pieza a las columnas de texto."""
    Worker = apps.get_model('workers', 'Worker')
    queryset = Worker.objects.filter(current_room__isnull=False).select_related(
        'current_room', 'current_room__camp'
    )
    for worker in queryset.iterator(chunk_size=2000):
        Worker.objects.filter(pk=worker.pk).update(
            camp=worker.current_room.camp.name[:30],
            room=worker.current_room.number[:20],
        )


class Migration(migrations.Migration):

    dependencies = [
        ('camps', '0001_initial'),
        ('workers', '0001_initial'),
    ]

    operations = [
        # El orden importa: primero existe la relación nueva, luego se traspasan
        # los datos, y solo al final se borran las columnas de texto.
        migrations.AddField(
            model_name='worker',
            name='current_room',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='occupants',
                to='camps.room',
            ),
        ),
        migrations.RunPython(migrate_camp_room_to_room, restore_camp_room),
        migrations.RemoveField(model_name='worker', name='camp'),
        migrations.RemoveField(model_name='worker', name='room'),
    ]
