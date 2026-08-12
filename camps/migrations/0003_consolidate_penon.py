from django.db import migrations

# Nombre de la faena y del cliente que agrupan todo el histórico. Toda la base
# corresponde a la faena Peñón, y a quien se le factura es Peñón: las 44
# entidades que hoy figuran como clientes son contratistas suyos.
FAENA_NAME = 'PEÑÓN'
CLIENT_NAME = 'PEÑÓN'
CLIENT_PREFIX = 'P'


def consolidate(apps, schema_editor):
    """Deja un cliente, una faena, y una sola fila por campamento y por puerta.

    La carga de datos creó un cliente por contratista (44). Como los
    campamentos colgaban del cliente, cada contratista se llevó su propia copia
    de los campamentos físicos de Peñón: 373 registros para 139 lugares reales,
    y 641 puertas con más de un QR. Esta migración deshace esa duplicación y
    repunta todo lo que apuntaba a las copias.
    """
    Client = apps.get_model('companies', 'Client')
    Company = apps.get_model('companies', 'Company')
    Faena = apps.get_model('camps', 'Faena')
    Camp = apps.get_model('camps', 'Camp')
    Room = apps.get_model('camps', 'Room')
    Worker = apps.get_model('workers', 'Worker')
    LaundryOrder = apps.get_model('orders', 'LaundryOrder')
    SiteScan = apps.get_model('orders', 'SiteScan')

    if not Camp.objects.exists() and not Client.objects.exists():
        return  # base vacía (tests, entorno limpio)

    # 1. Cliente único al que se le factura.
    client = Client.objects.filter(name__iexact=CLIENT_NAME).first()
    if client is None:
        client = Client.objects.create(
            name=CLIENT_NAME, reference_prefix=CLIENT_PREFIX, is_single_company=False, is_active=True
        )
    elif not client.reference_prefix:
        client.reference_prefix = CLIENT_PREFIX
        client.save(update_fields=['reference_prefix'])

    Company.objects.exclude(client_id=client.id).update(client_id=client.id)

    # 2. Faena única, dueña de los campamentos.
    faena, _ = Faena.objects.get_or_create(name=FAENA_NAME, defaults={'is_active': True})
    Camp.objects.update(faena_id=faena.id)

    # 3. Un campamento por nombre físico y una puerta por número dentro de él.
    #    Gana siempre el registro de menor id, que es el que conserva su
    #    `qr_code` —o sea, la etiqueta que ya podría estar pegada en la puerta—.
    #
    #    Campamentos y habitaciones se resuelven en la misma pasada porque no
    #    son independientes: al absorber las piezas de una copia, la pieza "19"
    #    puede existir ya en el campamento superviviente. En ese caso no se
    #    mueve, se fusiona.
    survivors: dict[str, int] = {}          # nombre de campamento -> id ganador
    rooms_by_camp: dict[int, dict[str, int]] = {}  # camp ganador -> {número: id ganador}
    merges: list[tuple[int, int]] = []      # (habitación perdedora, habitación ganadora)

    for camp in Camp.objects.order_by('id'):
        key = camp.name.strip().upper()
        winner_id = survivors.setdefault(key, camp.id)
        known = rooms_by_camp.setdefault(winner_id, {})

        for room in Room.objects.filter(camp_id=camp.id).order_by('id'):
            number = room.number.strip().upper()
            if number in known:
                merges.append((room.id, known[number]))
            else:
                known[number] = room.id
                if room.camp_id != winner_id:
                    Room.objects.filter(id=room.id).update(camp_id=winner_id)

    # 4. Repuntar todo lo que apuntaba a una puerta perdedora antes de borrarla,
    #    para no perder ninguna guía, pistoleo ni trabajador.
    for old_id, new_id in merges:
        LaundryOrder.objects.filter(delivery_room_id=old_id).update(delivery_room_id=new_id)
        SiteScan.objects.filter(room_id=old_id).update(room_id=new_id)
        Worker.objects.filter(current_room_id=old_id).update(current_room_id=new_id)
    Room.objects.filter(id__in=[old for old, _ in merges]).delete()
    Camp.objects.exclude(id__in=survivors.values()).delete()

    # 5. Los clientes que eran contratistas quedan sin empresas.
    #
    #    ANTES de borrarlos hay que repuntar los campamentos: en este punto de
    #    la historia de migraciones `Camp.client` todavía existe y es CASCADE
    #    (recién se elimina en 0004), así que borrar un cliente arrastraría sus
    #    campamentos y, con ellos, sus habitaciones y el destino de las guías.
    Camp.objects.update(client_id=client.id)
    Client.objects.exclude(id=client.id).delete()

    # 6. Red de seguridad: si algo de lo anterior borró de más, revienta aquí y
    #    la transacción revierte la migración completa en vez de dejar la base
    #    a medio consolidar.
    if Camp.objects.count() != len(survivors):
        raise RuntimeError(
            f'Se esperaban {len(survivors)} campamentos tras consolidar y quedaron {Camp.objects.count()}.'
        )
    if not Room.objects.exists():
        raise RuntimeError('La consolidación dejó la tabla de habitaciones vacía.')


class Migration(migrations.Migration):

    dependencies = [
        ('camps', '0002_faena_camp_faena'),
        ('companies', '0007_client_reference_prefix_remove_company_prefix'),
        ('orders', '0010_orderitem_label_code_and_more'),
        ('workers', '0001_initial'),
    ]

    operations = [
        # Irreversible a propósito: la duplicación era el error, no un estado al
        # que tenga sentido volver.
        migrations.RunPython(consolidate, migrations.RunPython.noop),
    ]
