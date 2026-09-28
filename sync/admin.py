from django.contrib import admin

from sync.models import Node, SyncIssue


@admin.register(Node)
class NodeAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active', 'last_push_at', 'last_pull_at', 'reported_pending')
    readonly_fields = ('token_hash', 'pull_cursor', 'last_push_at', 'last_pull_at', 'reported_pending')


@admin.register(SyncIssue)
class SyncIssueAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'kind', 'table', 'row_id', 'detail', 'resolved_at')
    list_filter = ('kind', 'table')
