from django.db import models

from common.models import TimeStampedModel


class Client(TimeStampedModel):
    """Cliente de Servilion: agrupa una o varias empresas (contratistas/mandantes).

    Es la entidad que se factura. El catálogo de precios y el cobro viven a este
    nivel (ver `ClientGarmentPrice` y `orders.LaundryOrder.billed_amount`): todas
    las empresas del cliente se cobran con el mismo catálogo.

    Hay dos casos que se modelan igual, con un solo camino de código:
    - El cliente agrupa varias empresas (ej. una faena/mandante con varios
      contratistas).
    - El cliente coincide con una única empresa (`is_single_company=True`): sigue
      siendo un cliente con una sola empresa hija, para que reportería, filtros y
      cobro no necesiten un caso especial. La UI usa la bandera para mostrarlo
      como una entidad en vez de un grupo anidado.
    """

    name = models.CharField(max_length=100, unique=True)
    tax_id = models.CharField('RUT cliente', max_length=15, blank=True)
    contact_name = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    is_single_company = models.BooleanField(
        'Cliente = empresa',
        default=False,
        help_text='El cliente tiene una sola empresa creada junto a él; la UI lo muestra como una única entidad.',
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Cliente'
        verbose_name_plural = 'Clientes'
        ordering = ['name']

    def __str__(self) -> str:
        return self.name


class Company(TimeStampedModel):
    """Empresa (contratista/mandante) cuyos trabajadores usan el servicio de lavandería.

    Siempre pertenece a un `Client`. El precio ya no vive aquí ni en el catálogo
    global de prendas: se define por cliente en `ClientGarmentPrice`.
    """

    class BillingType(models.TextChoices):
        PER_GARMENT = 'PRENDAS', 'Por prenda'
        PER_KG = 'KILOS', 'Por kilo'

    class DeliveryFlow(models.TextChoices):
        """Modalidad de contrato (FLUJO_NEGOCIO.md §2).

        FLUJO_1 entrega el morral al trabajador en su habitación y registra esa
        entrega; FLUJO_2 entrega al mandante sin trazabilidad individual, por lo
        que la guía pasa de despachada directo a cobrada.
        """

        WITH_ROOM_DELIVERY = 'FLUJO_1', 'Flujo 1 - entrega en habitación'
        CLIENT_ONLY = 'FLUJO_2', 'Flujo 2 - entrega solo al cliente'

    client = models.ForeignKey(Client, on_delete=models.PROTECT, related_name='companies')
    name = models.CharField(max_length=100, unique=True)
    tax_id = models.CharField('RUT empresa', max_length=15, blank=True)
    billing_type = models.CharField(max_length=10, choices=BillingType.choices, default=BillingType.PER_GARMENT)
    delivery_flow = models.CharField(
        'Modalidad de entrega', max_length=10, choices=DeliveryFlow.choices, default=DeliveryFlow.WITH_ROOM_DELIVERY
    )
    reference_prefix = models.CharField(
        'Prefijo del ref',
        max_length=3,
        blank=True,
        help_text='Inicial de faena/empresa que antecede al correlativo semanal del ref (ej. "P" en P1238).',
    )
    contact_name = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    logo_key = models.CharField('Objeto S3 del logo', max_length=255, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Empresa'
        verbose_name_plural = 'Empresas'
        ordering = ['name']
        indexes = [models.Index(fields=['client', 'is_active'], name='company_client_active_idx')]

    def __str__(self) -> str:
        return self.name


class ClientGarmentPrice(TimeStampedModel):
    """Precio de un tipo de prenda para un cliente (catálogo de precios por cliente).

    Reemplaza el precio global que antes vivía en `garments.GarmentType.unit_price`:
    cada cliente define su propio precio y todas sus empresas se cobran con él. El
    monto de cada guía se congela al cobrarla (`orders.LaundryOrder.billed_amount`),
    leyendo este catálogo en ese momento; cambiarlo después no altera lo ya cobrado.
    """

    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name='garment_prices')
    garment_type = models.ForeignKey('garments.GarmentType', on_delete=models.PROTECT, related_name='client_prices')
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Precio de prenda por cliente'
        verbose_name_plural = 'Catálogo de precios por cliente'
        ordering = ['client', 'garment_type']
        constraints = [
            models.UniqueConstraint(fields=['client', 'garment_type'], name='unique_client_garment_price'),
        ]

    def __str__(self) -> str:
        return f'{self.client.name} · {self.garment_type.name}: {self.unit_price}'
