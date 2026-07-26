"""Importa los datos legados de Access (ya exportados a JSONL) a PostgreSQL.

PASO 2 de la migración. Se ejecuta DENTRO del contenedor (donde vive Django,
PostgreSQL y todas las dependencias), consumiendo los .jsonl que produjo
`scripts/export_mdb.py` en el host Windows. Todo el mapeo al modelo nuevo vive
aquí, usando exclusivamente el ORM de Django.

Uso:
    docker compose exec api_servilion python manage.py import_legacy legacy_export
    docker compose exec api_servilion python manage.py import_legacy legacy_export --limit 500   # prueba
    docker compose exec api_servilion python manage.py import_legacy legacy_export --with-history

Es IDEMPOTENTE: puede re-ejecutarse sin duplicar datos. Las entidades ya
existentes se omiten (empresas/prendas/trabajadores por clave natural; guías por
`order_number`). Por eso NO usa `client_uuid` como clave de deduplicación (ese
campo se autogenera por guía y sólo sirve para la sincronización de la app).

Reglas de negocio aplicadas (ver FLUJO_NEGOCIO.md):
    - empresa (texto) -> Company (name único). billing_type desde `cobro`.
    - prenda -> GarmentType. Prendas nombradas en `item` que no estén en el
      catálogo se autocrean (marcadas is_active=False para revisarlas después).
    - usuario/trabajador -> Worker, clave natural (company, badge_code=codigo).
      Se crean también los trabajadores que sólo aparecen en `guias`.
    - guia -> LaundryOrder + OrderItem (parseando el texto libre `item`).
      order_number = ot si es único y no vacío; si `ot` viene vacío ("0", "00",
      "000000") se guarda None (el trabajador no escribió OT física, misma
      convención que usa `orders.services.normalize_order_number` para guías
      nuevas); si `ot` está duplicado con otra guía se conserva y se desambigua
      con '{ot}-L{IdAccess}'.
    - El staff legado (digitadopor/revisadopor) NO se mapea a
      received_by/reviewed_by: son nombres sueltos sin cuenta de
      usuario. Se preservan en las observaciones para trazabilidad. `pesadopor`
      no se registra: no es necesario trazar quién pesó el morral.
"""
from __future__ import annotations

import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from companies.models import Client, Company
from camps.models import Camp, Room
from garments.models import GarmentType
from orders.models import LaundryOrder, OrderItem, OrderStatus, OrderStatusHistory
from workers.models import Worker

# Mapa de estados legados -> nuevos. Las llaves están normalizadas (upper/trim).
STATUS_MAP = {
    "": OrderStatus.RECEIVED,
    "COBRADO": OrderStatus.BILLED,
    "COMPLETO": OrderStatus.COMPLETED,
    "CHECK": OrderStatus.QUALITY_CHECK,
    "CH3ECK": OrderStatus.QUALITY_CHECK,  # typo real presente en la data
    # DESPACHADA se eliminó del modelo nuevo (absorbida por COMPLETADA: no había
    # ningún pistoleo que representara "salió de planta" — ver OrderStatus).
    "DESPACHADO": OrderStatus.COMPLETED,
    "INCOMPLETO": OrderStatus.INCOMPLETE,
}

FALLBACK_COMPANY = "SIN EMPRESA (LEGADO)"

# Un token de `item` es "<cantidad> <nombre prenda>", separados por '+'.
ITEM_TOKEN_RE = re.compile(r"^\s*(\d+)\s+(.+?)\s*$")


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def norm(text) -> str:
    """Normaliza para comparar/deduplicar: sin acentos, mayúsculas, espacios colapsados."""
    if text is None:
        return ""
    return re.sub(r"\s+", " ", strip_accents(str(text)).upper()).strip()


def truncate(value, length: int) -> str:
    return ("" if value is None else str(value)).strip()[:length]


def to_aware(iso_value):
    """Convierte una fecha ISO naive del Access a datetime aware (America/Santiago)."""
    if not iso_value:
        return None
    dt = parse_datetime(iso_value)
    if dt is None:
        return None
    # Descarta fechas centinela del legado (0/'' -> año 1899/1900).
    if dt.year < 1990:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def to_decimal(value, quant="0.01"):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value)).quantize(Decimal(quant))
    except (InvalidOperation, ValueError):
        return None


def to_int(value) -> int:
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return 0


class RoomResolver:
    """Resuelve los `patio`/`pieza` del legado a `camps.Room`.

    El maestro legado trae ~280.000 filas con el campamento y la pieza escritos
    a mano, así que se cachea todo en memoria: sin esto serían dos consultas por
    trabajador importado.
    """

    def __init__(self):
        self._client_by_company: dict[int, int] = {}
        self._camps: dict[tuple[int, str], int] = {}
        self._rooms: dict[tuple[int, str], int] = {}

    def _client_id(self, company_id: int) -> int:
        if company_id not in self._client_by_company:
            self._client_by_company[company_id] = Company.objects.values_list(
                "client_id", flat=True
            ).get(pk=company_id)
        return self._client_by_company[company_id]

    def resolve(self, company_id: int, camp: str, room: str) -> int | None:
        camp, room = (camp or "").strip(), (room or "").strip()
        # Sin campamento no hay puerta que identificar: el trabajador queda sin
        # habitación hasta que alguien la registre en el panel.
        if not camp or not room:
            return None

        client_id = self._client_id(company_id)
        camp_key = (client_id, camp)
        if camp_key not in self._camps:
            camp_obj, _ = Camp.objects.get_or_create(client_id=client_id, name=camp)
            self._camps[camp_key] = camp_obj.id

        room_key = (self._camps[camp_key], room)
        if room_key not in self._rooms:
            room_obj, _ = Room.objects.get_or_create(
                camp_id=self._camps[camp_key], number=room
            )
            self._rooms[room_key] = room_obj.id
        return self._rooms[room_key]


def read_jsonl(path: Path):
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


class Command(BaseCommand):
    help = "Importa los datos legados de Access (JSONL) a PostgreSQL respetando el modelo actual."

    def add_arguments(self, parser):
        parser.add_argument("export_dir", help="Carpeta con los .jsonl (ej: legacy_export)")
        parser.add_argument("--batch-size", type=int, default=2000)
        parser.add_argument("--limit", type=int, default=0, help="Máx. de guías a importar (0 = todas). Para pruebas.")
        parser.add_argument(
            "--with-history",
            action="store_true",
            help="Crea una entrada en OrderStatusHistory marcando el estado migrado de cada guía.",
        )

    def handle(self, *args, **opts):
        base = Path(opts["export_dir"])
        if not base.is_dir():
            raise CommandError(f"No existe la carpeta {base.resolve()}")
        self.batch_size = opts["batch_size"]
        self.limit = opts["limit"]
        self.with_history = opts["with_history"]
        self.rooms = RoomResolver()

        orders_file = base / "orders.jsonl"
        self._load_companies(base / "companies.jsonl", orders_file, base / "workers.jsonl")
        self._load_garments(base / "garments.jsonl")
        self._load_workers(base / "workers.jsonl", orders_file)
        if orders_file.exists():
            self._load_orders(orders_file)
        self.stdout.write(self.style.SUCCESS("Importación completada."))

    # ---- Empresas ---------------------------------------------------------
    def _load_companies(self, companies_file: Path, orders_file: Path, workers_file: Path):
        self.stdout.write("Cargando empresas...")
        billing = {
            Company.BillingType.PER_GARMENT: Company.BillingType.PER_GARMENT,
            Company.BillingType.PER_KG: Company.BillingType.PER_KG,
        }
        # Nombre canónico por clave normalizada, para no crear duplicados por acentos.
        canon: dict[str, str] = {}
        cobro_by_norm: dict[str, str] = {}

        def register(name, cobro=None):
            name = truncate(name, 100)
            if not name:
                name = FALLBACK_COMPANY
            key = norm(name)
            canon.setdefault(key, name)
            if cobro:
                cobro_by_norm[key] = norm(cobro)

        if companies_file.exists():
            for row in read_jsonl(companies_file):
                register(row.get("empresa"), row.get("cobro"))
        # Empresas que sólo aparecen denormalizadas en workers/guias.
        for f in (workers_file, orders_file):
            if f.exists():
                for row in read_jsonl(f):
                    register(row.get("empresa"))
        register(FALLBACK_COMPANY)

        existing = {norm(n): pk for pk, n in Company.objects.values_list("id", "name")}
        # Cada empresa nueva estrena un cliente 1:1 (caso cliente=empresa): así
        # toda empresa queda bajo un cliente y el resto del sistema no necesita un
        # caso especial. Más adelante se pueden reagrupar empresas bajo un cliente
        # compartido desde el panel.
        existing_clients = {norm(n): pk for pk, n in Client.objects.values_list("id", "name")}
        to_create = []
        for key, name in canon.items():
            if key in existing:
                continue
            client_id = existing_clients.get(key)
            if client_id is None:
                client_id = Client.objects.create(name=name, is_single_company=True).pk
                existing_clients[key] = client_id
            bt = billing.get(cobro_by_norm.get(key), Company.BillingType.PER_GARMENT)
            to_create.append(Company(name=name, client_id=client_id, billing_type=bt))
        Company.objects.bulk_create(to_create, batch_size=self.batch_size)

        self.company_by_norm = {norm(n): pk for pk, n in Company.objects.values_list("id", "name")}
        self.stdout.write(f"  empresas: {len(self.company_by_norm)} en total ({len(to_create)} nuevas)")

    def _company_id(self, name):
        key = norm(name) or norm(FALLBACK_COMPANY)
        return self.company_by_norm.get(key) or self.company_by_norm[norm(FALLBACK_COMPANY)]

    # ---- Prendas (catálogo) ----------------------------------------------
    def _load_garments(self, garments_file: Path):
        self.stdout.write("Cargando tipos de prenda...")
        self.garment_by_code: dict[str, int] = {}
        self.garment_by_name: dict[str, int] = {}  # clave normalizada -> pk
        self._used_codes: set[str] = set()

        for code, name in GarmentType.objects.values_list("code", "name"):
            self._used_codes.add(code.upper())

        if garments_file.exists():
            for row in read_jsonl(garments_file):
                code = truncate(row.get("codigo"), 10).upper()
                name = truncate(row.get("prenda"), 60)
                if not name:
                    continue
                if not code or code in self._used_codes:
                    code = self._gen_code(name)
                # El precio legado (`valor`) ya no vive en el catálogo global: el
                # precio se define por cliente en companies.ClientGarmentPrice.
                obj, _ = GarmentType.objects.get_or_create(code=code, defaults={"name": name})
                self._used_codes.add(obj.code.upper())
                self._register_garment(obj)

        # Indexa también lo que ya existiera en la BD.
        for obj in GarmentType.objects.all():
            self._register_garment(obj)
        self.stdout.write(f"  tipos de prenda: {len(self.garment_by_code)}")

    def _register_garment(self, obj: GarmentType):
        self.garment_by_code[obj.code.upper()] = obj.id
        self.garment_by_name.setdefault(norm(obj.name), obj.id)
        self._used_codes.add(obj.code.upper())

    def _gen_code(self, name: str) -> str:
        base = re.sub(r"[^A-Z0-9]", "", norm(name))[:8] or "PRENDA"
        code = base[:10]
        i = 1
        while code in self._used_codes:
            suffix = str(i)
            code = (base[: 10 - len(suffix)] + suffix)
            i += 1
        return code

    def _garment_id(self, name: str) -> int:
        """Devuelve el id del tipo de prenda; lo autocrea si el nombre es nuevo."""
        key = norm(name)
        pk = self.garment_by_name.get(key)
        if pk:
            return pk
        code = self._gen_code(name)
        obj = GarmentType.objects.create(code=code, name=truncate(name, 60), is_active=False)
        self._register_garment(obj)
        return obj.id

    # ---- Trabajadores -----------------------------------------------------
    def _load_workers(self, workers_file: Path, orders_file: Path):
        self.stdout.write("Cargando trabajadores...")
        # clave natural: (company_id, badge_code) -> datos denormalizados
        pending: dict[tuple[int, str], dict] = {}

        def add(row, from_orders: bool):
            badge = truncate(row.get("codigo"), 20)
            if not badge:
                return
            name = truncate(row.get("nombre"), 100)
            if not from_orders and norm(name) == "A" and norm(badge) == "A":
                return  # fila basura del maestro legado
            company_id = self._company_id(row.get("empresa"))
            key = (company_id, badge)
            pending.setdefault(key, {
                "company_id": company_id,
                "badge_code": badge,
                "full_name": name or badge,
                "national_id": truncate(row.get("rut"), 15),
                "current_room_id": self.rooms.resolve(
                    company_id, truncate(row.get("patio"), 100), truncate(row.get("pieza"), 20)
                ),
                "shift": truncate(row.get("turno"), 10),
                "position": truncate(row.get("cargo"), 50),
                "area": truncate(row.get("area"), 50),
                "phone": truncate(row.get("telefono"), 20),
            })

        if workers_file.exists():
            for row in read_jsonl(workers_file):
                add(row, from_orders=False)
        if orders_file.exists():
            for row in read_jsonl(orders_file):
                add(row, from_orders=True)

        existing = set(Worker.objects.values_list("company_id", "badge_code"))
        to_create = [Worker(**data) for key, data in pending.items() if key not in existing]
        Worker.objects.bulk_create(to_create, batch_size=self.batch_size)

        self.worker_by_key = {
            (c, b): pk for pk, c, b in Worker.objects.values_list("id", "company_id", "badge_code")
        }
        self.stdout.write(f"  trabajadores: {len(self.worker_by_key)} en total ({len(to_create)} nuevos)")

    # ---- Guías ------------------------------------------------------------
    def _load_orders(self, orders_file: Path):
        self.stdout.write("Cargando guías (esto puede tardar)...")
        # None (sin OT física) se excluye del set de colisión: muchas guías
        # distintas pueden compartir "sin OT" sin ser duplicados entre sí.
        used_numbers = {
            n for n in LaundryOrder.objects.values_list("order_number", flat=True) if n is not None
        }
        created = skipped = 0
        buffer: list[tuple[LaundryOrder, dict]] = []

        for i, row in enumerate(read_jsonl(orders_file)):
            if self.limit and i >= self.limit:
                break
            order_number = self._resolve_order_number(row, used_numbers)
            if order_number is not None:
                if order_number in used_numbers:
                    skipped += 1
                    continue
                used_numbers.add(order_number)

            company_id = self._company_id(row.get("empresa"))
            badge = truncate(row.get("codigo"), 20)
            worker_id = self.worker_by_key.get((company_id, badge))
            if worker_id is None:  # red de seguridad: crea el trabajador faltante
                worker_id = self._create_missing_worker(company_id, badge, row)

            order = self._build_order(row, order_number, company_id, worker_id)
            buffer.append((order, row))
            if len(buffer) >= self.batch_size:
                created += self._flush(buffer)
                buffer = []
                self.stdout.write(f"  ...{created} guías creadas", ending="\r")

        if buffer:
            created += self._flush(buffer)
        self.stdout.write("")
        self.stdout.write(f"  guías: {created} creadas, {skipped} omitidas (ya existían)")

    def _resolve_order_number(self, row, used: set) -> str | None:
        ot = truncate(row.get("ot"), 20)
        legacy_id = row.get("id")
        if ot and ot not in ("0", "00", "000000") and ot not in used:
            return ot
        if not ot or ot in ("0", "00", "000000"):
            # El trabajador no escribió OT en el papel físico: no hay nada
            # real que preservar (misma convención que
            # `orders.services.normalize_order_number` para guías nuevas).
            return None
        # `ot` existe pero ya lo usa otra guía: se conserva el número real y
        # se desambigua con el id de Access.
        return truncate(f"{ot}-L{legacy_id}", 20)

    def _build_order(self, row, order_number, company_id, worker_id) -> LaundryOrder:
        status = STATUS_MAP.get(norm(row.get("status")), OrderStatus.RECEIVED)
        received = to_aware(row.get("recepcion")) or to_aware(row.get("rlavanderia")) or to_aware(row.get("entrega"))
        if received is None:
            received = timezone.make_aware(timezone.datetime(1900, 1, 1), timezone.get_current_timezone())

        observations = truncate(row.get("observacion"), 100000)
        legacy_staff = " · ".join(
            f"{label}: {row[field]}"
            for label, field in (("Digitó", "digitadopor"), ("Revisó", "revisadopor"))
            if row.get(field)
        )
        if legacy_staff:
            observations = (observations + "\n" if observations else "") + f"[Legado] {legacy_staff}"

        billed = to_aware(row.get("entrega")) if status == OrderStatus.BILLED else None

        return LaundryOrder(
            order_number=order_number,
            ticket_number=truncate(row.get("ticket"), 20),
            worker_id=worker_id,
            company_id=company_id,
            shift=truncate(row.get("turno"), 10),
            status=status,
            garment_count=to_int(row.get("prendas")),
            weight_kg=to_decimal(row.get("peso")),
            received_at=received,
            completed_at=to_aware(row.get("completado")) or to_aware(row.get("despachado")),
            delivered_at=to_aware(row.get("entregado")),
            billed_at=billed,
            observations=observations,
            reference=truncate(row.get("ref"), 20),
            control_code=truncate(row.get("control"), 20),
        )

    def _build_items(self, order: LaundryOrder, row) -> list[OrderItem]:
        """Parsea el texto libre `item` a filas OrderItem, agregando duplicados."""
        text = row.get("item")
        if not text:
            return []
        qty_by_garment: dict[int, int] = {}
        for token in str(text).split("+"):
            m = ITEM_TOKEN_RE.match(token)
            if not m:
                continue
            qty = to_int(m.group(1))
            name = m.group(2).strip()
            if qty <= 0 or not name:
                continue
            gid = self._garment_id(name)
            qty_by_garment[gid] = qty_by_garment.get(gid, 0) + qty
        return [
            OrderItem(order_id=order.id, garment_type_id=gid, quantity=qty)
            for gid, qty in qty_by_garment.items()
        ]

    @transaction.atomic
    def _flush(self, buffer: list[tuple[LaundryOrder, dict]]) -> int:
        orders = [o for o, _ in buffer]
        LaundryOrder.objects.bulk_create(orders, batch_size=self.batch_size)
        # En PostgreSQL bulk_create asigna el pk a cada instancia -> ya podemos
        # crear los detalles y el historial referenciando order.id.
        items: list[OrderItem] = []
        history: list[OrderStatusHistory] = []
        for order, row in buffer:
            items.extend(self._build_items(order, row))
            if self.with_history:
                history.append(OrderStatusHistory(
                    order_id=order.id,
                    previous_status="",
                    new_status=order.status,
                    note="Migrado desde Access (histórico)",
                ))
        if items:
            OrderItem.objects.bulk_create(items, batch_size=self.batch_size, ignore_conflicts=True)
        if history:
            OrderStatusHistory.objects.bulk_create(history, batch_size=self.batch_size)
        return len(orders)

    def _create_missing_worker(self, company_id, badge, row) -> int:
        obj = Worker.objects.create(
            company_id=company_id,
            badge_code=badge or "S/C",
            full_name=truncate(row.get("nombre"), 100) or (badge or "S/C"),
            national_id=truncate(row.get("rut"), 15),
            current_room_id=self.rooms.resolve(
                company_id, truncate(row.get("patio"), 100), truncate(row.get("pieza"), 20)
            ),
            shift=truncate(row.get("turno"), 10),
            position=truncate(row.get("cargo"), 50),
        )
        self.worker_by_key[(company_id, obj.badge_code)] = obj.id
        return obj.id
