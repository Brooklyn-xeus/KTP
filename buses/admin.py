from django.contrib import admin
from .models import Route, Bus, Stop, RouteStop

@admin.register(Stop)
class StopAdmin(admin.ModelAdmin):
    list_display = ['name', 'lat', 'lng']
    search_fields = ['name']

@admin.register(Route)
class RouteAdmin(admin.ModelAdmin):
    list_display = ['name', 'start_point', 'end_point']

@admin.register(RouteStop)
class RouteStopAdmin(admin.ModelAdmin):
    list_display = ['route', 'stop', 'order']
    list_filter = ['route']

@admin.register(Bus)
class BusAdmin(admin.ModelAdmin):
    list_display = ['plate_number', 'route', 'driver', 'is_active']
    list_filter = ['is_active']
    list_editable = ['is_active']
