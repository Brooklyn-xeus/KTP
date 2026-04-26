from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User

@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ['phone', 'email', 'name', 'is_driver',
                    'is_approved', 'docs_verified', 'is_active']
    list_filter = ['is_driver', 'is_approved', 'docs_verified']
    list_editable = ['is_approved', 'docs_verified']
    search_fields = ['phone', 'name', 'email', 'bus_number']
    ordering = ['-created_at']
    fieldsets = (
        (None, {'fields': ('phone', 'email', 'name', 'pin')}),
        ('Google Auth', {'fields': ('google_id', 'device_fingerprint')}),
        ('Driver Docs', {'fields': (
            'license_no', 'rc_number', 'bus_number', 'selfie_url'
        )}),
        ('Status', {'fields': (
            'is_driver', 'is_approved', 'docs_verified',
            'is_active', 'is_staff', 'is_superuser'
        )}),
        ('FCM', {'fields': ('fcm_token',)}),
    )
    add_fieldsets = (
        (None, {'fields': (
            'phone', 'name', 'pin', 'is_driver'
        )}),
    )
    filter_horizontal = []