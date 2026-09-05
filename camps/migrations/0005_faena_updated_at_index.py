"""Indexa `Faena.updated_at`, deriva que quedó pendiente desde hace tiempo.

`TimeStampedModel.updated_at` (common/models.py) declara `db_index=True` desde
que existe: la app móvil offline-first lo usa para resolver conflictos contra
`last_sync`. Todos los demás modelos que heredan de esa base ya tenían su
migración aplicada; solo `Faena` se quedó atrás. No tiene relación con el
trabajo de esta tanda — se generó al correr `makemigrations` para otra cosa y
se separa en su propio archivo para no mezclar un índice preexistente con
cambios nuevos.

Segura de aplicar en cualquier momento: solo agrega un índice, sin tocar datos.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [('camps', '0004_camp_faena_required')]

    operations = [
        migrations.AlterField(
            model_name='faena',
            name='updated_at',
            field=models.DateTimeField(auto_now=True, db_index=True),
        ),
    ]
