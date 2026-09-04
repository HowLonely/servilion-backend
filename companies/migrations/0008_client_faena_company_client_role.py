import django.db.models.deletion
from django.db import migrations, models


def set_initial_roles_and_faena(apps, schema_editor):
    """Clasifica las empresas existentes y hereda la faena si solo hay una.

    Antes de este cambio el modelo no distinguía a la empresa del propio cliente
    de sus contratistas. El único dato que ya lo insinuaba es
    `Client.is_single_company`: si el cliente tiene una sola empresa creada junto
    a él, esa empresa ES el cliente, así que queda MANDANTE; el resto son
    contratistas (el default del campo).

    La faena se asigna sola solo cuando hay exactamente una en la base —el caso
    real de hoy, donde toda la operación es Peñón—. Con dos o más no hay forma de
    adivinar cuál corresponde a cada cliente y se deja en blanco para
    configurarla desde el panel.
    """
    Client = apps.get_model('companies', 'Client')
    Company = apps.get_model('companies', 'Company')
    Faena = apps.get_model('camps', 'Faena')

    Company.objects.filter(client__is_single_company=True).update(client_role='MANDANTE')

    faenas = list(Faena.objects.all()[:2])
    if len(faenas) == 1:
        Client.objects.update(faena=faenas[0])


def clear_faena(apps, schema_editor):
    Client = apps.get_model('companies', 'Client')
    Client.objects.update(faena=None)


class Migration(migrations.Migration):

    dependencies = [
        ('camps', '0004_camp_faena_required'),
        ('companies', '0007_client_reference_prefix_remove_company_prefix'),
    ]

    operations = [
        migrations.AddField(
            model_name='client',
            name='faena',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='clients',
                to='camps.faena',
                verbose_name='Faena',
            ),
        ),
        migrations.AddField(
            model_name='company',
            name='client_role',
            field=models.CharField(
                choices=[
                    ('MANDANTE', 'Mandante (se lava directo al cliente)'),
                    ('CONTRATISTA', 'Contratista'),
                ],
                default='CONTRATISTA',
                max_length=12,
                verbose_name='Tipo',
            ),
        ),
        migrations.RunPython(set_initial_roles_and_faena, clear_faena),
    ]
