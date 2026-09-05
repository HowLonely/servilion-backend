"""Ensancha la letra de ciclo del `ref`.

`next_cycle` cuenta en base 26 sin techo (Z -> AA -> ... -> ZZ -> AAA), pero la
columna admitia 2 caracteres: al agotar ZZ, generar un ref reventaba con un
DataError. Son 702 ciclos x 1000 numeros = 702.000 refs por prefijo, asi que
nunca se alcanzo; se corrige igual porque era una rotura garantizada y no un
limite de diseno.

Es seguro de aplicar en cualquier momento: solo agranda la columna.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [('orders', '0016_backfill_resolution_shipped_at')]

    operations = [
        migrations.AlterField(
            model_name='referencecounter',
            name='cycle',
            field=models.CharField(default='A', max_length=4, verbose_name='Letra de ciclo'),
        ),
    ]
