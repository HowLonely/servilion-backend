from django.contrib import admin

from camps.models import Camp, Room


@admin.register(Camp)
class CampAdmin(admin.ModelAdmin):
    list_display = ('name', 'client', 'is_active')
    list_filter = ('client', 'is_active')
    search_fields = ('name',)


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ('number', 'camp', 'qr_code', 'is_active')
    list_filter = ('camp', 'is_active')
    search_fields = ('number', 'qr_code')
    readonly_fields = ('qr_code',)
