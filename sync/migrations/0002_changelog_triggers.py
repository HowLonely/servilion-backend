"""Instala los triggers que anotan en `sync_changelog` cada cambio de las tablas sincronizadas.

Corre en los dos nodos. La lista de tablas se congela aquí (no se importa del
registro) porque una migración tiene que producir siempre el mismo esquema; una
tabla nueva que deba sincronizarse necesita su propia migración que llame a
`trigger_sql`.
"""

from django.db import migrations

from sync.triggers import DROP_FUNCTION_SQL, FUNCTION_SQL, drop_trigger_sql, trigger_sql

TABLES = (
    'authentication_staffrole',
    'authentication_user',
    'camps_faena',
    'camps_camp',
    'camps_room',
    'garments_garmenttype',
    'companies_client',
    'companies_company',
    'companies_clientgarmentprice',
    'workers_worker',
    'weighing_weighingsettings',
    'orders_referencecounter',
    'hospitality_dispatchcounter',
    'orders_laundryorder',
    'orders_orderitem',
    'weighing_weighin',
    'weighing_weighlabel',
    'orders_missingitemresolution',
    'orders_orderstatushistory',
    'orders_sitescan',
    'orders_syncconflict',
    'hospitality_linenmovement',
    'hospitality_linenmovementline',
)


class Migration(migrations.Migration):

    dependencies = [
        ('sync', '0001_initial'),
        ('authentication', '0004_staff_roles'),
        ('camps', '0005_faena_updated_at_index'),
        ('companies', '0008_client_faena_company_client_role'),
        ('garments', '0003_garmenttype_is_linen'),
        ('hospitality', '0002_linen_stock_movements'),
        ('orders', '0020_laundryorder_service_type'),
        ('weighing', '0002_express_service_type'),
        ('workers', '0002_worker_current_room'),
    ]

    operations = [
        migrations.RunSQL(
            sql=[FUNCTION_SQL, *[trigger_sql(table) for table in TABLES]],
            reverse_sql=[*[drop_trigger_sql(table) for table in TABLES], DROP_FUNCTION_SQL],
        ),
    ]
