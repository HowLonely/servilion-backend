from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0018_unique_reference'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitescan',
            name='accuracy_meters',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='sitescan',
            name='client_uuid',
            field=models.UUIDField(blank=True, editable=False, null=True, unique=True),
        ),
        migrations.AddField(
            model_name='sitescan',
            name='latitude',
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True),
        ),
        migrations.AddField(
            model_name='sitescan',
            name='longitude',
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True),
        ),
        migrations.AlterField(
            model_name='sitescan',
            name='kind',
            field=models.CharField(
                choices=[
                    ('RECEPCION_SUCIA', 'Recepción ropa sucia en faena'),
                    ('RECEPCION_LIMPIA', 'Recepción ropa limpia en faena'),
                    ('ENTREGA', 'Entrega'),
                ],
                max_length=20,
            ),
        ),
    ]