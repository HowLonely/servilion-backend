from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    """Introduce la faena y prepara el traslado de los campamentos.

    Se hace en tres migraciones y no en una porque el campo `faena` nace
    obligatorio: primero se crea nullable, después una migración de datos lo
    llena y deduplica, y recién entonces se cierra la restricción.
    """

    dependencies = [
        ('camps', '0001_initial'),
        ('companies', '0007_client_reference_prefix_remove_company_prefix'),
    ]

    operations = [
        migrations.CreateModel(
            name='Faena',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=100, unique=True, verbose_name='Nombre')),
                ('is_active', models.BooleanField(default=True)),
            ],
            options={
                'verbose_name': 'Faena',
                'verbose_name_plural': 'Faenas',
                'ordering': ['name'],
            },
        ),
        migrations.RemoveConstraint(model_name='camp', name='unique_camp_per_client'),
        migrations.RemoveIndex(model_name='camp', name='camp_client_active_idx'),
        migrations.AddField(
            model_name='camp',
            name='faena',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='camps',
                to='camps.faena',
            ),
        ),
    ]
