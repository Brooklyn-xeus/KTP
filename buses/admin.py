from django.contrib import admin
from .models import Route, Bus

@admin.register(Route)
class RouteAdmin(admin.ModelAdmin):
    list_display = ['name', 'start_point', 'end_point']

@admin.register(Bus)
class BusAdmin(admin.ModelAdmin):
    list_display = ['plate_number', 'route', 'driver', 'is_active']
    list_filter = ['is_active']
    list_editable = ['is_active']

# Register your models here.
