from collections import Counter

from companies.models import Company
from garments.models import GarmentType
from orders.models import LaundryOrder, OrderItem
from workers.models import Worker

print("orders:", LaundryOrder.objects.count(), "items:", OrderItem.objects.count())
print("companies:", Company.objects.count(), "workers:", Worker.objects.count(),
      "garments:", GarmentType.objects.count(),
      "autocreadas(inactivas):", GarmentType.objects.filter(is_active=False).count())
print("status dist:", dict(Counter(LaundryOrder.objects.values_list("status", flat=True))))

o = (LaundryOrder.objects.select_related("worker", "company")
     .prefetch_related("items__garment_type").first())
print("\nGUIA:", o.order_number, "| status", o.status, "| empresa", o.company.name,
      "| trab", o.worker.full_name)
print("recibida:", o.received_at, "| completada:", o.completed_at,
      "| entregada:", o.delivered_at, "| peso", o.weight_kg, "| prendas(legado)", o.garment_count)
print("obs:", repr(o.observations))
for it in o.items.all():
    print("   -", it.quantity, "x", it.display_name)
print("suma items:", sum(i.quantity for i in o.items.all()))
