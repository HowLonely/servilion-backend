from django.db import migrations, models


class Migration(migrations.Migration):
    """Suma el rol PESAJE (báscula de recepción en Antofagasta).

    Solo cambian las opciones del campo: no hay dato que migrar, porque ninguna
    cuenta existente pesaba antes de que la estación existiera.
    """

    dependencies = [('authentication', '0002_restructure_roles')]

    operations = [
        migrations.AlterField(
            model_name='user',
            name='role',
            field=models.CharField(
                choices=[
                    ('ADMIN', 'Administrador'),
                    ('SUPERVISOR', 'Supervisor'),
                    ('PESAJE', 'Pesaje'),
                    ('DIGITADOR_OT', 'Digitador de OT'),
                    ('DIGITADOR_EMPAQUE', 'Digitador de Empaque'),
                ],
                default='DIGITADOR_OT',
                max_length=20,
            ),
        ),
    ]
