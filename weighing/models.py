from django.conf import settings
from django.db import models

from common.models import TimeStampedModel
from orders.models import ServiceType

# Cupo con que nace la configuración. Solo es el valor inicial de la fila: el
# límite vigente se lee siempre de `WeighingSettings` y se cambia desde la web.
DEFAULT_EXPRESS_MONTHLY_LIMIT = 300


class WeighIn(TimeStampedModel):
    """Pesaje del morral sucio al llegar a la lavandería (FLUJO_NEGOCIO.md §4, paso 4).

    Es lo primero que ocurre en Antofagasta, antes de digitalizar: se pesa el
    morral completo, se cuenta cuántas prendas trae y se declara a quién se le
    factura (cliente) y de qué empresa es (mandante o contratista). Con eso se
    emite el `ref` y se imprime un adhesivo lavable por prenda más un ticket
    maestro que viaja con el morral hasta la mesa de digitación.

    Vive como modelo propio y no como una `LaundryOrder` a medio llenar por dos
    razones que se sostienen solas:

    - En la báscula **no se sabe quién es el trabajador**: eso viene escrito en
      la OT física que se digitaliza después. `LaundryOrder.worker` es
      obligatorio y no tiene sentido volverlo opcional para toda la historia del
      sistema por un estado que dura minutos.
    - Un morral puede pesarse y no digitalizarse nunca (se traspapela, se anula).
      El kilo igual entró a la planta y debe quedar contado.

    El `ref` se genera **aquí** y no al digitalizar porque es lo que va impreso
    en los adhesivos que ya están pegados a la ropa: el identificador que viaja
    con la prenda tiene que ser el mismo que usa el resto del flujo. La
    contrapartida asumida es que un pesaje anulado quema su correlativo.
    """

    class Status(models.TextChoices):
        PENDING = 'PENDIENTE', 'Pendiente de digitalizar'
        DIGITIZED = 'DIGITALIZADA', 'Digitalizada'
        VOIDED = 'ANULADA', 'Anulada'

    reference = models.CharField('Ref', max_length=20, unique=True)

    # El cliente es a quién se le factura y el dueño del prefijo del `ref`; la
    # empresa es de quién es la ropa. Se guardan los dos aunque `company.client`
    # ya lo implique: en la báscula el operador elige explícitamente ambos, y
    # dejar constancia de lo que eligió evita que una reasignación posterior de
    # la empresa a otro cliente reescriba la historia de un pesaje ya facturado.
    client = models.ForeignKey(
        'companies.Client', on_delete=models.PROTECT, related_name='weigh_ins', verbose_name='Cliente'
    )
    company = models.ForeignKey(
        'companies.Company', on_delete=models.PROTECT, related_name='weigh_ins', verbose_name='Empresa'
    )

    garment_count = models.PositiveIntegerField('Prendas contadas en báscula')
    # Peso bruto tal cual lo muestra la balanza, morral incluido: no se descuenta
    # tara. Mismos límites que `LaundryOrder.weight_kg` para que la copia al
    # digitalizar no pueda desbordar.
    weight_kg = models.DecimalField('Peso del morral (kg)', max_digits=6, decimal_places=2)

    status = models.CharField(max_length=15, choices=Status.choices, default=Status.PENDING, db_index=True)
    # Lo elige el operador al cerrar el pesaje. Los EXPRESS consumen el cupo
    # mensual de `WeighingSettings` mientras no estén anulados.
    service_type = models.CharField(
        'Tipo de cargo', max_length=10, choices=ServiceType.choices, default=ServiceType.NORMAL
    )

    weighed_at = models.DateTimeField('Momento del pesaje', db_index=True)
    weighed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )

    # La guía que terminó consumiendo este pesaje. SET_NULL y no CASCADE: borrar
    # una guía no debe borrar la evidencia de que ese morral se pesó.
    order = models.OneToOneField(
        'orders.LaundryOrder',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='weigh_in',
        verbose_name='Guía digitalizada',
    )
    digitized_at = models.DateTimeField(null=True, blank=True)

    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    void_reason = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = 'Pesaje'
        verbose_name_plural = 'Pesajes'
        ordering = ['-weighed_at']
        indexes = [
            # La báscula lista "los pesajes de mi turno" y la digitación busca
            # "los pendientes": las dos consultas filtran por estado y ordenan
            # por fecha.
            models.Index(fields=['status', 'weighed_at'], name='weigh_status_at_idx'),
            # El contador del cupo express cuenta por tipo dentro del mes, y se
            # consulta en cada pesaje express y cada 30 s desde cada báscula.
            models.Index(fields=['service_type', 'weighed_at'], name='weigh_service_at_idx'),
        ]

    def __str__(self) -> str:
        return f'{self.reference} · {self.garment_count} prendas · {self.weight_kg} kg'

    @property
    def is_open(self) -> bool:
        """Se puede anular o reimprimir mientras nadie lo haya digitalizado."""
        return self.status == self.Status.PENDING


class WeighingSettings(models.Model):
    """Configuración de la báscula editable desde el panel web. Fila única (pk=1).

    Es un singleton en base de datos y no una variable de entorno porque la
    cambia un administrador desde la web, sin redeploy. Además la fila hace de
    candado: `create_weigh_in` la bloquea antes de contar el cupo express, así
    dos básculas no pueden llevarse a la vez el último cargo del mes.
    """

    express_monthly_limit = models.PositiveIntegerField(
        'Cupo mensual de cargos express', default=DEFAULT_EXPRESS_MONTHLY_LIMIT
    )
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )

    class Meta:
        verbose_name = 'Configuración de pesaje'
        verbose_name_plural = 'Configuración de pesaje'

    def __str__(self) -> str:
        return f'Cupo express: {self.express_monthly_limit}/mes'

    @classmethod
    def load(cls) -> 'WeighingSettings':
        settings_row, _ = cls.objects.get_or_create(pk=1)
        return settings_row


class WeighLabel(models.Model):
    """Un adhesivo lavable, uno por prenda física del morral.

    En la báscula solo se conoce el **total** de prendas, no de qué tipo es cada
    una (eso lo declara el digitador después, leyendo la OT física). Por eso la
    etiqueta identifica una unidad anónima —`P1375A-03`— y no un tipo de prenda.

    Que cada unidad tenga código propio cierra un agujero del esquema anterior,
    donde todas las unidades de un tipo compartían adhesivo (`P1375A-TOA`): se
    podía pistolear cuatro veces la MISMA polera y el sistema daba por vueltas
    las cuatro. Con una etiqueta por unidad, el segundo disparo sobre la misma
    prenda es un duplicado detectable.
    """

    weigh_in = models.ForeignKey(WeighIn, on_delete=models.CASCADE, related_name='labels')
    sequence = models.PositiveSmallIntegerField('N° de prenda dentro del morral')
    # Se guarda materializado (y no compuesto al vuelo) porque es lo que lee la
    # pistola: un índice sobre esta columna resuelve el escaneo en una consulta.
    code = models.CharField(max_length=30, unique=True)

    scanned_at = models.DateTimeField('Pistoleada en empaque', null=True, blank=True)
    scanned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )

    class Meta:
        verbose_name = 'Etiqueta de prenda'
        verbose_name_plural = 'Etiquetas de prenda'
        ordering = ['weigh_in', 'sequence']
        constraints = [
            models.UniqueConstraint(fields=['weigh_in', 'sequence'], name='weigh_label_unique_sequence'),
        ]

    def __str__(self) -> str:
        return self.code
