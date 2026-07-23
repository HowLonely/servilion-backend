from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('companies', '0004_link_companies_to_clients'),
    ]

    operations = [
        migrations.AlterField(
            model_name='company',
            name='client',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='companies',
                to='companies.client',
            ),
        ),
    ]
