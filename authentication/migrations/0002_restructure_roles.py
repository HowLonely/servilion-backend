from django.db import migrations, models

# Reestructuración de los roles operativos: se pasa de cinco roles por área
# (RECEPCION / LAVANDERIA / DESPACHO) a dos roles por estación de digitalización,
# que es como está organizado el trabajo real en Antofagasta.
ROLE_MIGRATION = {
    'RECEPCION': 'DIGITADOR_OT',
    'LAVANDERIA': 'DIGITADOR_EMPAQUE',
    'DESPACHO': 'DIGITADOR_EMPAQUE',
}

# LAVANDERIA y DESPACHO colapsan en el mismo rol nuevo, así que la vuelta atrás
# no puede ser exacta: se elige DESPACHO por ser el que tenía los permisos de
# empaque y entrega.
ROLE_ROLLBACK = {
    'DIGITADOR_OT': 'RECEPCION',
    'DIGITADOR_EMPAQUE': 'DESPACHO',
}


def _remap(apps, mapping):
    User = apps.get_model('authentication', 'User')
    for old_role, new_role in mapping.items():
        User.objects.filter(role=old_role).update(role=new_role)


def migrate_roles(apps, schema_editor):
    _remap(apps, ROLE_MIGRATION)


def rollback_roles(apps, schema_editor):
    _remap(apps, ROLE_ROLLBACK)


class Migration(migrations.Migration):
    dependencies = [
        ('authentication', '0001_initial'),
    ]

    operations = [
        # Los datos se remapean ANTES de cambiar las choices para que ningún
        # usuario quede con un rol que ya no existe.
        migrations.RunPython(migrate_roles, rollback_roles),
        migrations.AlterField(
            model_name='user',
            name='role',
            field=models.CharField(
                choices=[
                    ('ADMIN', 'Administrador'),
                    ('SUPERVISOR', 'Supervisor'),
                    ('DIGITADOR_OT', 'Digitador de OT'),
                    ('DIGITADOR_EMPAQUE', 'Digitador de Empaque'),
                ],
                default='DIGITADOR_OT',
                max_length=20,
            ),
        ),
    ]
