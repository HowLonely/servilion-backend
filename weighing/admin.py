from django.contrib import admin

from .models import WeighIn, WeighLabel


class WeighLabelInline(admin.TabularInline):
    model = WeighLabel
    extra = 0
    fields = ('sequence', 'code', 'scanned_at', 'scanned_by')
    readonly_fields = ('sequence', 'code')
    can_delete = False


@admin.register(WeighIn)
class WeighInAdmin(admin.ModelAdmin):
    list_display = ('reference', 'client', 'company', 'garment_count', 'weight_kg', 'status', 'weighed_at')
    list_filter = ('status', 'client')
    search_fields = ('reference', 'company__name', 'client__name')
    date_hierarchy = 'weighed_at'
    autocomplete_fields = ('client', 'company')
    # El ref y el momento del pesaje son hechos consumados: se emiten en la
    # báscula y andan impresos en adhesivos pegados a la ropa.
    readonly_fields = ('reference', 'weighed_at', 'weighed_by', 'digitized_at', 'voided_at', 'voided_by')
    inlines = [WeighLabelInline]
