from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0005_laundryorder_ord_incomplete_at_idx_and_more'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='orderitem',
            name='unit_price',
        ),
        migrations.AddField(
            model_name='laundryorder',
            name='billed_amount',
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=12, null=True, verbose_name='Monto cobrado'
            ),
        ),
    ]
