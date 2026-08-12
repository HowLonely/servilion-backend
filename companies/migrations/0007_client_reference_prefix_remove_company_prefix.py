from django.db import migrations, models


def move_prefix_to_client(apps, schema_editor):
    """Traslada el prefijo del ref de la empresa a su cliente.

    Solo 6 de 44 empresas lo tenían configurado, y el ref siempre identificó a
    quién se le factura, no a la contratista.
    """
    Company = apps.get_model('companies', 'Company')
    for company in Company.objects.exclude(reference_prefix='').select_related('client'):
        client = company.client
        if not client.reference_prefix:
            client.reference_prefix = company.reference_prefix
            client.save(update_fields=['reference_prefix'])


class Migration(migrations.Migration):

    dependencies = [('companies', '0006_company_service_type')]

    operations = [
        migrations.AddField(
            model_name='client',
            name='reference_prefix',
            field=models.CharField(
                blank=True,
                help_text='Inicial del cliente que antecede al correlativo del ref (ej. "P" en P1375A).',
                max_length=3,
                verbose_name='Prefijo del ref',
            ),
        ),
        migrations.RunPython(move_prefix_to_client, migrations.RunPython.noop),
        migrations.RemoveField(model_name='company', name='reference_prefix'),
    ]
