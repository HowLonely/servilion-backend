from django.contrib import admin

from companies.models import Client, ClientGarmentPrice, Company


class ClientGarmentPriceInline(admin.TabularInline):
    model = ClientGarmentPrice
    extra = 0
    autocomplete_fields = ('garment_type',)


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_single_company', 'is_active', 'updated_at')
    list_filter = ('is_single_company', 'is_active')
    search_fields = ('name', 'tax_id')
    inlines = (ClientGarmentPriceInline,)


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ('name', 'client', 'billing_type', 'is_active', 'updated_at')
    list_filter = ('billing_type', 'is_active', 'client')
    search_fields = ('name', 'tax_id')
    autocomplete_fields = ('client',)


@admin.register(ClientGarmentPrice)
class ClientGarmentPriceAdmin(admin.ModelAdmin):
    list_display = ('client', 'garment_type', 'unit_price', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('client__name', 'garment_type__name', 'garment_type__code')
    autocomplete_fields = ('client', 'garment_type')
