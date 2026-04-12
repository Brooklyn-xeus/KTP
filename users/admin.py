from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User

@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ['phone', 'name', 'is_driver', 'is_approved', 'is_active']
    list_filter = ['is_driver', 'is_approved']
    list_editable = ['is_approved']
    search_fields = ['phone', 'name']
    ordering = ['-created_at']
    fieldsets = (
        (None, {'fields': ('phone', 'name', 'password')}),
        ('Role', {'fields': ('is_driver', 'is_approved')}),
        ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser')}),
        ('FCM', {'fields': ('fcm_token',)}),
    )
    add_fieldsets = (
        (None, {'fields': ('phone', 'name', 'password1', 'password2', 'is_driver')}),
    )
    filter_horizontal = []

# Register your models here.
