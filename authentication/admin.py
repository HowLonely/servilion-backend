from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from authentication.models import StaffRole, User


@admin.register(User)
class ServilionUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ('Servilion', {'fields': ('role', 'phone')}),
    )
    list_display = ('username', 'first_name', 'last_name', 'role', 'is_active')
    list_filter = ('role', 'is_active')


@admin.register(StaffRole)
class StaffRoleAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'is_system', 'is_active')
    search_fields = ('name', 'code')
