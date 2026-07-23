from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('garments', '0001_initial'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='garmenttype',
            name='unit_price',
        ),
    ]
