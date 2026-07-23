from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('garments', '0001_initial'),
        ('companies', '0002_company_delivery_flow_company_reference_prefix'),
    ]

    operations = [
        migrations.CreateModel(
            name='Client',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True, db_index=True)),
                ('name', models.CharField(max_length=100, unique=True)),
                ('tax_id', models.CharField(blank=True, max_length=15, verbose_name='RUT cliente')),
                ('contact_name', models.CharField(blank=True, max_length=100)),
                ('phone', models.CharField(blank=True, max_length=20)),
                ('is_single_company', models.BooleanField(default=False, help_text='El cliente tiene una sola empresa creada junto a él; la UI lo muestra como una única entidad.', verbose_name='Cliente = empresa')),
                ('is_active', models.BooleanField(default=True)),
            ],
            options={
                'verbose_name': 'Cliente',
                'verbose_name_plural': 'Clientes',
                'ordering': ['name'],
            },
        ),
        migrations.AddField(
            model_name='company',
            name='client',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='companies',
                to='companies.client',
            ),
        ),
        migrations.AddIndex(
            model_name='company',
            index=models.Index(fields=['client', 'is_active'], name='company_client_active_idx'),
        ),
        migrations.CreateModel(
            name='ClientGarmentPrice',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True, db_index=True)),
                ('unit_price', models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ('is_active', models.BooleanField(default=True)),
                ('client', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='garment_prices', to='companies.client')),
                ('garment_type', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='client_prices', to='garments.garmenttype')),
            ],
            options={
                'verbose_name': 'Precio de prenda por cliente',
                'verbose_name_plural': 'Catálogo de precios por cliente',
                'ordering': ['client', 'garment_type'],
            },
        ),
        migrations.AddConstraint(
            model_name='clientgarmentprice',
            constraint=models.UniqueConstraint(fields=('client', 'garment_type'), name='unique_client_garment_price'),
        ),
    ]
