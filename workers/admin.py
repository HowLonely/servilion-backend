from django.contrib import admin

from workers.models import Worker


@admin.register(Worker)
class WorkerAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'badge_code', 'company', 'camp', 'shift', 'is_active')
    list_filter = ('company', 'shift', 'is_active')
    search_fields = ('full_name', 'national_id', 'badge_code')
    autocomplete_fields = ('company',)
