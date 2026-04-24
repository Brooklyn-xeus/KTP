from django.db import models
from django.utils import timezone
from buses.models import Bus, Route, Stop
from users.models import User

class Trip(models.Model):
    STATUS = [
        ('active', 'Active'),
        ('paused', 'Paused'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
        ('stale', 'Stale'),
    ]
    driver = models.ForeignKey(User, on_delete=models.CASCADE)
    bus = models.ForeignKey(Bus, on_delete=models.CASCADE)
    route = models.ForeignKey(Route, on_delete=models.CASCADE)
    start_time = models.DateTimeField(auto_now_add=True)
    end_time = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS, default='active')
    is_paused = models.BooleanField(default=False)
    passenger_count = models.IntegerField(default=0)

    def __str__(self):
        return f"Trip {self.id} — {self.driver.name} — {self.status}"

class BusLocation(models.Model):
    bus = models.OneToOneField(Bus, on_delete=models.CASCADE, related_name='location')
    trip = models.ForeignKey(Trip, on_delete=models.SET_NULL, null=True, blank=True)
    lat = models.FloatField()
    lng = models.FloatField()
    speed = models.FloatField(default=0.0)
    last_updated = models.DateTimeField(auto_now=True)

    def is_fresh(self):
        return (timezone.now() - self.last_updated).total_seconds() <= 60

    def __str__(self):
        return f"Bus {self.bus.plate_number} @ {self.lat},{self.lng}"

class PassengerWaiting(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    route = models.ForeignKey(Route, on_delete=models.CASCADE)
    from_stop = models.ForeignKey(Stop, on_delete=models.CASCADE, related_name='waiting_from', null=True, blank=True)
    to_stop = models.ForeignKey(Stop, on_delete=models.CASCADE, related_name='waiting_to', null=True, blank=True)
    lat = models.FloatField()
    lng = models.FloatField()
    got_bus = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.name} waiting on {self.route.name}"

class LocationSharingSession(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    from_stop = models.ForeignKey(Stop, on_delete=models.CASCADE, related_name='sessions_from')
    to_stop = models.ForeignKey(Stop, on_delete=models.CASCADE, related_name='sessions_to')
    lat = models.FloatField()
    lng = models.FloatField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.name} sharing — {self.from_stop} → {self.to_stop}"

class Subscription(models.Model):
    TIME_CHOICES = [('AM', 'Morning'), ('PM', 'Evening')]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='subscriptions')
    route = models.ForeignKey(Route, on_delete=models.CASCADE)
    from_stop = models.ForeignKey(Stop, on_delete=models.CASCADE, related_name='sub_from', null=True, blank=True)
    to_stop = models.ForeignKey(Stop, on_delete=models.CASCADE, related_name='sub_to', null=True, blank=True)
    time_window = models.CharField(max_length=2, choices=TIME_CHOICES)
    daily_time = models.TimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['user', 'route', 'time_window']

    def __str__(self):
        return f"{self.user.name} → {self.route.name} ({self.time_window})"

class NotificationLog(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    route = models.ForeignKey(Route, on_delete=models.CASCADE)
    bus = models.ForeignKey(Bus, on_delete=models.CASCADE)
    time_window = models.CharField(max_length=2)
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['user', 'route', 'time_window']

class DriverFrequentRoute(models.Model):
    driver = models.ForeignKey(User, on_delete=models.CASCADE)
    route = models.ForeignKey(Route, on_delete=models.CASCADE)
    usage_count = models.IntegerField(default=1)
    last_used = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['driver', 'route']
        ordering = ['-usage_count']
class UserRoutePreference(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='route_preference')
    from_stop = models.ForeignKey(Stop, on_delete=models.CASCADE, related_name='pref_from')
    to_stop = models.ForeignKey(Stop, on_delete=models.CASCADE, related_name='pref_to')
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.name}: {self.from_stop} → {self.to_stop}"

class StaleTrip(models.Model):
    trip = models.OneToOneField(Trip, on_delete=models.CASCADE)
    marked_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Stale: {self.trip}"
class EmergencyAlert(models.Model):
    driver = models.ForeignKey(User, on_delete=models.CASCADE, related_name='emergencies')
    trip = models.ForeignKey(Trip, on_delete=models.CASCADE)
    latitude = models.FloatField()
    longitude = models.FloatField()
    timestamp = models.DateTimeField(auto_now_add=True)
    resolved = models.BooleanField(default=False)
    notes = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"Emergency — {self.driver.name} @ {self.timestamp}"

class PassengerCountLog(models.Model):
    trip = models.ForeignKey(Trip, on_delete=models.CASCADE, related_name='count_logs')
    count = models.IntegerField()
    logged_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Trip {self.trip.id} — {self.count} passengers"

class StopArrival(models.Model):
    trip = models.ForeignKey(Trip, on_delete=models.CASCADE, related_name='arrivals')
    stop = models.ForeignKey(Stop, on_delete=models.CASCADE)
    arrival_time = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('trip', 'stop')

    def __str__(self):
        return f"Trip {self.trip.id} arrived at {self.stop.name}"
