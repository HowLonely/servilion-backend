from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    """Cierra el traslado: la faena pasa a obligatoria y el cliente desaparece.

    La restricción de unicidad vuelve a proteger el nivel correcto: un solo
    campamento por nombre dentro de la faena, sin importar a quién se le
    facture. Es lo que impide que la duplicación se repita.
    """

    dependencies = [('camps', '0003_consolidate_penon')]

    operations = [
        migrations.AlterField(
            model_name='camp',
            name='faena',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='camps',
                to='camps.faena',
            ),
        ),
        migrations.RemoveField(model_name='camp', name='client'),
        migrations.AlterModelOptions(
            name='camp',
            options={
                'ordering': ['faena', 'name'],
                'verbose_name': 'Campamento',
                'verbose_name_plural': 'Campamentos',
            },
        ),
        migrations.AddConstraint(
            model_name='camp',
            constraint=models.UniqueConstraint(fields=('faena', 'name'), name='unique_camp_per_faena'),
        ),
        migrations.AddIndex(
            model_name='camp',
            index=models.Index(fields=['faena', 'is_active'], name='camp_faena_active_idx'),
        ),
    ]
