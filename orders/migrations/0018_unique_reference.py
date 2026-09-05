"""Vuelve estructural la unicidad del `ref`.

Hasta ahora la garantizaba solo `generate_reference` con su contador bloqueado,
y dos vias mas podian escribir un ref arbitrario: `LaundryOrderIn.reference` y
`OrderSyncIn.reference`. Ambas se cerraron; esta constraint es el respaldo, para
que la unicidad no dependa de que cada cliente se porte bien.

Las guias importadas de Access traen refs del esquema semanal anterior (sin
letra de ciclo), con ~272.000 repeticiones sobre 283.000 filas. La restriccion
se limita al formato moderno terminado en letra para conservar ese historico
sin renunciar a la garantia sobre los refs nuevos.

Los refs que emite el sistema actual no se repiten nunca: el contador es
estrictamente monotono y al dar la vuelta avanza la letra, asi que un morral
extraviado o una guia que nunca se cerro no pueden provocar que su ref se
reemita.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [('orders', '0017_referencecounter_cycle_length')]

    operations = [
        migrations.AddConstraint(
            model_name='laundryorder',
            constraint=models.UniqueConstraint(
                fields=('reference',),
                condition=models.Q(reference__regex=r'^[A-Z]{1,3}[0-9]{4}[A-Z]+$'),
                name='unique_reference_per_order',
            ),
        ),
    ]
