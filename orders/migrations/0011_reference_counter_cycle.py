import re

from django.db import migrations, models

# Rango del correlativo, igual que en orders.models.
SEQUENCE_START = 1000
SEQUENCE_END = 1999

REF_PATTERN = re.compile(r'^([A-Za-z]{1,3})(\d+)')


def seed_counters(apps, schema_editor):
    """Deja un contador por prefijo, sembrado por encima de lo que hay en circulación.

    Es el paso delicado del cambio. Los contadores guardados quedaron en 1006,
    pero hay refs impresos en morrales abiertos que llegan a ~1999: si el
    contador arrancara desde 1006 repartiría códigos que ya están puestos en una
    etiqueta, provocando justo las colisiones que este cambio viene a evitar.

    Por eso la siembra mira los refs de las guías todavía abiertas y arranca
    después del más alto. Si eso ya supera el tope del rango, se abre un ciclo
    nuevo, que es exactamente para lo que existe la letra.
    """
    ReferenceCounter = apps.get_model('orders', 'ReferenceCounter')
    LaundryOrder = apps.get_model('orders', 'LaundryOrder')

    ReferenceCounter.objects.all().delete()

    open_statuses = ['RECIBIDA', 'EN_REVISION', 'INCOMPLETA']
    highest: dict[str, int] = {}
    for reference in (
        LaundryOrder.objects.filter(status__in=open_statuses)
        .exclude(reference='')
        .values_list('reference', flat=True)
        .iterator(chunk_size=5000)
    ):
        match = REF_PATTERN.match(reference.strip())
        if not match:
            continue
        prefix = match.group(1).upper()
        number = int(match.group(2))
        if number > highest.get(prefix, 0):
            highest[prefix] = number

    for prefix, number in highest.items():
        if number >= SEQUENCE_END:
            # El rango quedó agotado por lo que ya está en circulación: se parte
            # de cero en el ciclo siguiente.
            ReferenceCounter.objects.create(prefix=prefix, cycle='B', last_number=SEQUENCE_START)
        else:
            ReferenceCounter.objects.create(
                prefix=prefix, cycle='A', last_number=max(number, SEQUENCE_START)
            )


class Migration(migrations.Migration):

    dependencies = [('orders', '0010_orderitem_label_code_and_more')]

    operations = [
        migrations.RemoveConstraint(model_name='referencecounter', name='unique_reference_counter_week'),
        migrations.RemoveField(model_name='referencecounter', name='iso_year'),
        migrations.RemoveField(model_name='referencecounter', name='iso_week'),
        migrations.AddField(
            model_name='referencecounter',
            name='cycle',
            field=models.CharField(default='A', max_length=2, verbose_name='Letra de ciclo'),
        ),
        migrations.AlterField(
            model_name='referencecounter',
            name='prefix',
            field=models.CharField(max_length=3, unique=True),
        ),
        migrations.RunPython(seed_counters, migrations.RunPython.noop),
    ]
