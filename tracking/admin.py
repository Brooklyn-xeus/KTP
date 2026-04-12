from django.contrib import admin
from .models import BusLocation, PassengerWaiting, Subscription, NotificationLog

@admin.register(BusLocation)
class BusLocationAdmin(admin.ModelAdmin):
    list_display = ['bus', 'lat', 'lng', 'last_updated']
    readonly_fields = ['last_updated']

@admin.register(PassengerWaiting)
class PassengerWaitingAdmin(admin.ModelAdmin):
    list_display = ['user', 'route', 'got_bus', 'created_at']
    list_filter = ['got_bus']

@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ['user', 'route', 'time_window', 'is_active']
    list_filter = ['time_window', 'is_active']

@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    list_display = ['user', 'route', 'bus', 'time_window', 'sent_at']
    list_filter = ['time_window']

# Register your models here.
