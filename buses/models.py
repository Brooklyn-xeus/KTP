from django.db import models
from users.models import User

class Stop(models.Model):
    name = models.CharField(max_length=100)
    lat = models.FloatField()
    lng = models.FloatField()

    def __str__(self):
        return self.name

class Route(models.Model):
    name = models.CharField(max_length=100)
    start_point = models.CharField(max_length=100)
    end_point = models.CharField(max_length=100)
    stops = models.ManyToManyField(Stop, through='RouteStop')

    def __str__(self):
        return f"{self.name} ({self.start_point} → {self.end_point})"

class RouteStop(models.Model):
    route = models.ForeignKey(Route, on_delete=models.CASCADE)
    stop = models.ForeignKey(Stop, on_delete=models.CASCADE)
    order = models.IntegerField()

    class Meta:
        ordering = ['order']
        unique_together = ['route', 'order']

    def __str__(self):
        return f"{self.route.name} - Stop {self.order}: {self.stop.name}"

class Bus(models.Model):
    route = models.ForeignKey(Route, on_delete=models.CASCADE, related_name='buses')
    driver = models.OneToOneField(User, on_delete=models.CASCADE, related_name='bus', null=True, blank=True)
    plate_number = models.CharField(max_length=20, unique=True)
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.plate_number} — {self.route.name}"
