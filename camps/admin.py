from django.contrib import admin

from camps.models import Camp, Faena, Room


@admin.register(Faena)
class FaenaAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name',)


@admin.register(Camp)
class CampAdmin(admin.ModelAdmin):
    list_display = ('name', 'faena', 'is_active')
    list_filter = ('faena', 'is_active')
    search_fields = ('name',)


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ('number', 'camp', 'qr_code', 'is_active')
    list_filter = ('camp', 'is_active')
    search_fields = ('number', 'qr_code')
    readonly_fields = ('qr_code',)
