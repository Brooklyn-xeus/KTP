#from django.db import models
#from django.utils import timezone
#from buses.models import Bus
#from users.models import User

#class BusLocation(models.Model):
#    bus = models.OneToOneField(Bus, on_delete=models.CASCADE, related_name='location')
#    lat = models.FloatField()
#    lng = models.FloatField()
#    last_updated = models.DateTimeField(auto_now=True)

#    def is_fresh(self):
#        return (timezone.now() - self.last_updated).seconds <= 60

#    def __str__(self):
#        return f"Bus {self.bus.plate_number} @ {self.lat},{self.lng}"

#class PassengerWaiting(models.Model):
#    user = models.ForeignKey(User, on_delete=models.CASCADE)
#    route = models.ForeignKey('buses.Route', on_delete=models.CASCADE)
#    lat = models.FloatField()
#    lng = models.FloatField()
#    got_bus = models.BooleanField(default=False)
#    created_at = models.DateTimeField(auto_now_add=True)

#    def __str__(self):
#        return f"{self.user.name} waiting on {self.route.name}"

#class Subscription(models.Model):
#    TIME_CHOICES = [('AM', 'Morning'), ('PM', 'Evening')]
#    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='subscriptions')
#    route = models.ForeignKey('buses.Route', on_delete=models.CASCADE)
#    time_window = models.CharField(max_length=2, choices=TIME_CHOICES)
#    is_active = models.BooleanField(default=True)
#    created_at = models.DateTimeField(auto_now_add=True)

#    class Meta:
#        unique_together = ['user', 'route', 'time_window']

#    def __str__(self):
#        return f"{self.user.name} → {self.route.name} ({self.time_window})"

# Create your models here.

from django.db import models
from django.utils import timezone
from buses.models import Bus, Route
from users.models import User

class BusLocation(models.Model):
    bus = models.OneToOneField(
        Bus, on_delete=models.CASCADE, related_name='location'
    )
    lat = models.FloatField()
    lng = models.FloatField()
    last_updated = models.DateTimeField(auto_now=True)

    def is_fresh(self):
        return (timezone.now() - self.last_updated).total_seconds() <= 60

    def __str__(self):
        return f"Bus {self.bus.plate_number} @ {self.lat},{self.lng}"

class PassengerWaiting(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    route = models.ForeignKey(Route, on_delete=models.CASCADE)
    lat = models.FloatField()
    lng = models.FloatField()
    got_bus = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.name} waiting on {self.route.name}"

class Subscription(models.Model):
    TIME_CHOICES = [('AM', 'Morning'), ('PM', 'Evening')]
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='subscriptions'
    )
    route = models.ForeignKey(Route, on_delete=models.CASCADE)
    time_window = models.CharField(max_length=2, choices=TIME_CHOICES)
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
        # Prevent duplicate notifications same day same window
        unique_together = ['user', 'route', 'time_window']

    def __str__(self):
        return f"Notified {self.user.name} for {self.route.name}"
        
