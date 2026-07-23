from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from authentication.models import User


@admin.register(User)
class ServilionUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ('Servilion', {'fields': ('role', 'phone')}),
    )
    list_display = ('username', 'first_name', 'last_name', 'role', 'is_active')
    list_filter = ('role', 'is_active')
