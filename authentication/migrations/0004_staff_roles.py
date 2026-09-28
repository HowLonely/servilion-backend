"""Roles editables: los cinco roles fijos pasan a ser filas con permisos.

Los permisos sembrados son exactamente lo que cada rol podía hacer cuando los
roles eran constantes en los decoradores de la API, así que nadie gana ni
pierde acceso con esta migración. Se copian aquí como texto (y no se importan de
`authentication.permissions`) para que la migración no cambie si el catálogo
cambia después.
"""

from django.db import migrations, models

SYSTEM_ROLES = {
    'ADMIN': ('Administrador', 'Acceso total al sistema.', []),
    'SUPERVISOR': (
        'Supervisor',
        'Toda la operación de planta y de faena, sin administrar el catálogo.',
        [
            'weighing.operate', 'orders.digitize', 'orders.pack', 'orders.dispatch', 'hospitality.view',
            'history.view', 'field.orders', 'field.hospitality', 'reports.view',
        ],
    ),
    'PESAJE': ('Pesaje', 'Báscula de recepción de la planta.', ['weighing.operate']),
    'DIGITADOR_OT': ('Digitador de OT', 'Digitalización de la OT física.', ['orders.digitize', 'history.view']),
    'DIGITADOR_EMPAQUE': (
        'Digitador de Empaque',
        'Empaque, despacho de morrales y despacho de lencería.',
        ['orders.pack', 'orders.dispatch', 'hospitality.dispatch', 'hospitality.view'],
    ),
}


def seed_roles(apps, schema_editor):
    StaffRole = apps.get_model('authentication', 'StaffRole')
    for code, (name, description, permissions) in SYSTEM_ROLES.items():
        StaffRole.objects.update_or_create(
            code=code,
            defaults={'name': name, 'description': description, 'permissions': permissions, 'is_system': True},
        )


class Migration(migrations.Migration):

    dependencies = [
        ('authentication', '0003_user_role_pesaje'),
    ]

    operations = [
        migrations.CreateModel(
            name='StaffRole',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True, db_index=True)),
                ('code', models.CharField(max_length=40, unique=True, verbose_name='Código')),
                ('name', models.CharField(max_length=60, verbose_name='Nombre')),
                ('description', models.CharField(blank=True, max_length=200, verbose_name='Descripción')),
                ('permissions', models.JSONField(blank=True, default=list, verbose_name='Permisos')),
                ('is_system', models.BooleanField(default=False)),
                ('is_active', models.BooleanField(default=True)),
            ],
            options={
                'verbose_name': 'Rol',
                'verbose_name_plural': 'Roles',
                'ordering': ['name'],
            },
        ),
        migrations.AddField(
            model_name='user',
            name='is_service_account',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='user',
            name='updated_at',
            field=models.DateTimeField(auto_now=True, db_index=True),
        ),
        migrations.AlterField(
            model_name='user',
            name='role',
            field=models.CharField(default='DIGITADOR_OT', max_length=40),
        ),
        migrations.RunPython(seed_roles, migrations.RunPython.noop),
    ]
