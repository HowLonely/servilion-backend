import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def create_settings_row(apps, schema_editor):
    # La fila única nace con el cupo por defecto (300) para que la báscula
    # tenga límite desde el primer pesaje, sin esperar a que alguien lo guarde
    # desde la web.
    WeighingSettings = apps.get_model('weighing', 'WeighingSettings')
    WeighingSettings.objects.get_or_create(pk=1, defaults={'express_monthly_limit': 300})


class Migration(migrations.Migration):

    dependencies = [
        ('weighing', '0001_initial'),
        ('orders', '0020_laundryorder_service_type'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='weighin',
            name='service_type',
            field=models.CharField(
                choices=[('NORMAL', 'Normal'), ('EXPRESS', 'Express')],
                default='NORMAL',
                max_length=10,
                verbose_name='Tipo de cargo',
            ),
        ),
        migrations.AddIndex(
            model_name='weighin',
            index=models.Index(fields=['service_type', 'weighed_at'], name='weigh_service_at_idx'),
        ),
        migrations.CreateModel(
            name='WeighingSettings',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                (
                    'express_monthly_limit',
                    models.PositiveIntegerField(default=300, verbose_name='Cupo mensual de cargos express'),
                ),
                ('updated_at', models.DateTimeField(auto_now=True)),
                (
                    'updated_by',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='+',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                'verbose_name': 'Configuración de pesaje',
                'verbose_name_plural': 'Configuración de pesaje',
            },
        ),
        migrations.RunPython(create_settings_row, migrations.RunPython.noop),
    ]
