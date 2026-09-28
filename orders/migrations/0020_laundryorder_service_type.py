from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0019_sitescan_delivery_location'),
    ]

    operations = [
        migrations.AddField(
            model_name='laundryorder',
            name='service_type',
            field=models.CharField(
                choices=[('NORMAL', 'Normal'), ('EXPRESS', 'Express')],
                db_index=True,
                default='NORMAL',
                max_length=10,
                verbose_name='Tipo de cargo',
            ),
        ),
    ]
