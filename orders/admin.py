from django.contrib import admin

from orders.models import (
    LaundryOrder,
    OrderItem,
    OrderStatusHistory,
    ReferenceCounter,
    SiteScan,
    SyncConflict,
)


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    autocomplete_fields = ('garment_type',)


class OrderStatusHistoryInline(admin.TabularInline):
    model = OrderStatusHistory
    extra = 0
    readonly_fields = ('previous_status', 'new_status', 'changed_by', 'changed_at', 'note')
    can_delete = False


@admin.register(LaundryOrder)
class LaundryOrderAdmin(admin.ModelAdmin):
    list_display = ('order_number', 'worker', 'company', 'status', 'received_at', 'garment_count')
    list_filter = ('status', 'company', 'shift')
    search_fields = ('order_number', 'ticket_number', 'worker__full_name', 'worker__national_id')
    autocomplete_fields = ('worker', 'company')
    inlines = (OrderItemInline, OrderStatusHistoryInline)


@admin.register(SiteScan)
class SiteScanAdmin(admin.ModelAdmin):
    list_display = ('scanned_code', 'kind', 'order', 'scanned_at', 'scanned_by')
    list_filter = ('kind',)
    search_fields = ('scanned_code', 'order__order_number')


@admin.register(SyncConflict)
class SyncConflictAdmin(admin.ModelAdmin):
    list_display = ('order', 'client_uuid', 'device_updated_at', 'server_updated_at', 'resolved_at')
    list_filter = ('resolved_at',)
    search_fields = ('order__order_number',)


@admin.register(ReferenceCounter)
class ReferenceCounterAdmin(admin.ModelAdmin):
    list_display = ('prefix', 'iso_year', 'iso_week', 'last_number')
    list_filter = ('prefix', 'iso_year')
