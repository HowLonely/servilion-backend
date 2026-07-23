from django.db import migrations


def create_one_to_one_clients(apps, schema_editor):
    """Crea un Cliente 1:1 por cada Empresa existente y las enlaza.

    Preserva el comportamiento actual (cada empresa era su propio "cliente")
    dejando toda empresa bajo un cliente. Más adelante se pueden reagrupar
    empresas bajo un cliente compartido desde el panel. `is_single_company=True`
    marca el caso cliente=empresa para que la UI lo muestre como una entidad.
    """
    Client = apps.get_model('companies', 'Client')
    Company = apps.get_model('companies', 'Company')

    existing = {name for name in Client.objects.values_list('name', flat=True)}
    for company in Company.objects.filter(client__isnull=True):
        name = company.name
        # Colisión improbable de nombre Empresa vs Cliente preexistente: sufija.
        if name in existing:
            name = f'{name} (cliente)'[:100]
        client = Client.objects.create(
            name=name,
            tax_id=company.tax_id,
            contact_name=company.contact_name,
            phone=company.phone,
            is_single_company=True,
            is_active=company.is_active,
        )
        existing.add(name)
        company.client = client
        company.save(update_fields=['client'])


def unlink_clients(apps, schema_editor):
    """Revierte: desenlaza y borra los clientes 1:1 creados."""
    Company = apps.get_model('companies', 'Company')
    Client = apps.get_model('companies', 'Client')
    Company.objects.update(client=None)
    Client.objects.filter(is_single_company=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('companies', '0003_client_clientgarmentprice_company_client'),
    ]

    operations = [
        migrations.RunPython(create_one_to_one_clients, unlink_clients),
    ]
