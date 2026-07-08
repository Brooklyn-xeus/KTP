from django.contrib import admin
from .models import UserRoutePreference, StaleTrip
from .models import (BusLocation, PassengerWaiting, Subscription,
                     NotificationLog, Trip, LocationSharingSession,
                     DriverFrequentRoute)
from .models import EmergencyAlert, PassengerCountLog, StopArrival

@admin.register(Trip)
class TripAdmin(admin.ModelAdmin):
    list_display = ['id', 'driver', 'bus', 'route', 'status', 'start_time']
    list_filter = ['status']

@admin.register(BusLocation)
class BusLocationAdmin(admin.ModelAdmin):
    list_display = ['bus', 'lat', 'lng', 'speed', 'last_updated']
    readonly_fields = ['last_updated']

@admin.register(PassengerWaiting)
class PassengerWaitingAdmin(admin.ModelAdmin):
    list_display = ['user', 'route', 'got_bus', 'created_at']

@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ['user', 'route', 'time_window', 'is_active']

@admin.register(LocationSharingSession)
class LocationSharingSessionAdmin(admin.ModelAdmin):
    list_display = ['user', 'from_stop', 'to_stop', 'is_active', 'created_at']

admin.site.register(NotificationLog)
admin.site.register(DriverFrequentRoute)
@admin.register(UserRoutePreference)
class UserRoutePrefAdmin(admin.ModelAdmin):
    list_display = ['user', 'from_stop', 'to_stop', 'updated_at']

admin.site.register(StaleTrip)

@admin.register(EmergencyAlert)
class EmergencyAlertAdmin(admin.ModelAdmin):
    list_display = ['driver', 'trip', 'latitude', 'longitude', 'resolved', 'timestamp']
    list_filter = ['resolved']
    list_editable = ['resolved']
    readonly_fields = ['timestamp']

@admin.register(StopArrival)
class StopArrivalAdmin(admin.ModelAdmin):
    list_display = ['trip', 'stop', 'arrival_time']
    readonly_fields = ['arrival_time']

admin.site.register(PassengerCountLog)
from .models import RideRequest, RideDriverOffer, NoShowLog, UserViolation

@admin.register(RideRequest)
class RideRequestAdmin(admin.ModelAdmin):
    list_display = ['id', 'passenger', 'driver', 'vehicle_type', 'status', 'estimated_fare', 'created_at']
    list_filter = ['status', 'vehicle_type', 'payment_status']
    search_fields = ['passenger__name', 'driver__name']
    readonly_fields = ['created_at', 'accepted_at', 'arrived_at', 'started_at', 'completed_at']

@admin.register(NoShowLog)
class NoShowAdmin(admin.ModelAdmin):
    list_display = ['passenger', 'ride', 'logged_at']

@admin.register(UserViolation)
class ViolationAdmin(admin.ModelAdmin):
    list_display = ['user', 'violation_type', 'created_at']

admin.site.register(RideDriverOffer)
