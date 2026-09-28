import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


def mark_linen_types_from_batches(apps, schema_editor):
    # Los lotes se borran en esta misma migración, pero los tipos de lencería
    # que usaban son justo los que circulan en hotelería. Marcarlos evita que,
    # tras el deploy, el despacho y el conteo arranquen sin tipos que ofrecer.
    LinenBatchItem = apps.get_model('hospitality', 'LinenBatchItem')
    GarmentType = apps.get_model('garments', 'GarmentType')
    used = LinenBatchItem.objects.exclude(garment_type=None).values_list('garment_type_id', flat=True)
    GarmentType.objects.filter(id__in=set(used)).update(is_linen=True)


class Migration(migrations.Migration):

    dependencies = [
        ('camps', '0005_faena_updated_at_index'),
        ('companies', '0008_client_faena_company_client_role'),
        ('garments', '0003_garmenttype_is_linen'),
        ('hospitality', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(mark_linen_types_from_batches, migrations.RunPython.noop),
        migrations.CreateModel(
            name='DispatchCounter',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('year', models.PositiveIntegerField(unique=True)),
                ('last_number', models.PositiveIntegerField(default=0)),
            ],
            options={
                'verbose_name': 'Correlativo de despacho',
                'verbose_name_plural': 'Correlativos de despacho',
            },
        ),
        migrations.RemoveField(
            model_name='linenbatchitem',
            name='batch',
        ),
        migrations.RemoveField(
            model_name='linenbatchitem',
            name='garment_type',
        ),
        migrations.CreateModel(
            name='LinenMovement',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True, db_index=True)),
                ('client_uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('kind', models.CharField(choices=[('DESPACHO', 'Despacho a faena'), ('REPARTO', 'Reparto a campamento'), ('RETIRO', 'Retiro de sucio'), ('CONTEO', 'Conteo de inventario')], db_index=True, max_length=10, verbose_name='Tipo')),
                ('number', models.CharField(blank=True, max_length=20, verbose_name='N° de despacho')),
                ('occurred_at', models.DateTimeField(db_index=True, verbose_name='Momento del movimiento')),
                ('note', models.TextField(blank=True)),
                ('latitude', models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True)),
                ('longitude', models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True)),
                ('accuracy_meters', models.FloatField(blank=True, null=True)),
                ('voided_at', models.DateTimeField(blank=True, null=True)),
                ('void_reason', models.CharField(blank=True, max_length=200)),
                ('camp', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='linen_movements', to='camps.camp')),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='linen_movements', to='companies.company')),
                ('registered_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('voided_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Movimiento de lencería',
                'verbose_name_plural': 'Movimientos de lencería',
                'ordering': ['-occurred_at', '-id'],
            },
        ),
        migrations.CreateModel(
            name='LinenMovementLine',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('quantity', models.PositiveIntegerField(verbose_name='Cantidad')),
                ('difference', models.IntegerField(blank=True, null=True, verbose_name='Diferencia del conteo')),
                ('garment_type', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='garments.garmenttype')),
                ('movement', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='lines', to='hospitality.linenmovement')),
            ],
            options={
                'verbose_name': 'Línea de movimiento',
                'verbose_name_plural': 'Líneas de movimiento',
            },
        ),
        migrations.DeleteModel(
            name='LinenBatch',
        ),
        migrations.DeleteModel(
            name='LinenBatchItem',
        ),
        migrations.AddIndex(
            model_name='linenmovement',
            index=models.Index(fields=['company', 'occurred_at'], name='linen_company_at_idx'),
        ),
        migrations.AddIndex(
            model_name='linenmovement',
            index=models.Index(fields=['camp', 'occurred_at'], name='linen_camp_at_idx'),
        ),
        migrations.AddConstraint(
            model_name='linenmovement',
            constraint=models.UniqueConstraint(condition=models.Q(('number', ''), _negated=True), fields=('number',), name='unique_linen_dispatch_number'),
        ),
        migrations.AddConstraint(
            model_name='linenmovement',
            constraint=models.CheckConstraint(check=models.Q(models.Q(('kind__in', ['REPARTO', 'RETIRO']), _negated=True), ('camp__isnull', False), _connector='OR'), name='linen_field_movement_has_camp'),
        ),
        migrations.AddConstraint(
            model_name='linenmovement',
            constraint=models.CheckConstraint(check=models.Q(models.Q(('kind', 'DESPACHO'), _negated=True), ('camp__isnull', True), _connector='OR'), name='linen_dispatch_has_no_camp'),
        ),
        migrations.AddConstraint(
            model_name='linenmovementline',
            constraint=models.UniqueConstraint(fields=('movement', 'garment_type'), name='unique_garment_type_per_movement'),
        ),
    ]
