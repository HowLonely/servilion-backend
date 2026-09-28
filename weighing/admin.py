from django.contrib import admin

from .models import WeighingSettings, WeighIn, WeighLabel


@admin.register(WeighingSettings)
class WeighingSettingsAdmin(admin.ModelAdmin):
    list_display = ('express_monthly_limit', 'updated_at', 'updated_by')
    readonly_fields = ('updated_at', 'updated_by')

    def has_add_permission(self, request):
        # Fila única: se crea en la migración y solo se edita.
        return not WeighingSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


class WeighLabelInline(admin.TabularInline):
    model = WeighLabel
    extra = 0
    fields = ('sequence', 'code', 'scanned_at', 'scanned_by')
    readonly_fields = ('sequence', 'code')
    can_delete = False


@admin.register(WeighIn)
class WeighInAdmin(admin.ModelAdmin):
    list_display = ('reference', 'client', 'company', 'garment_count', 'weight_kg', 'service_type', 'status', 'weighed_at')
    list_filter = ('status', 'service_type', 'client')
    search_fields = ('reference', 'company__name', 'client__name')
    date_hierarchy = 'weighed_at'
    autocomplete_fields = ('client', 'company')
    # El ref y el momento del pesaje son hechos consumados: se emiten en la
    # báscula y andan impresos en adhesivos pegados a la ropa.
    readonly_fields = ('reference', 'weighed_at', 'weighed_by', 'digitized_at', 'voided_at', 'voided_by')
    inlines = [WeighLabelInline]
