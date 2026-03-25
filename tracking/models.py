from django.db import models

class Route(models.Model):
    name = models.CharField(max_length=100)
    start_point = models.CharField(max_length=100)
    end_point = models.CharField(max_length=100)
    fare = models.DecimalField(max_digits=8, decimal_places=2)
    
    def __str__(self):
        return self.name

class Driver(models.Model):
    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=15)
    is_active = models.BooleanField(default=True)
    
    def __str__(self):
        return self.name

class Bus(models.Model):
    route = models.ForeignKey(Route, on_delete=models.CASCADE)
    driver = models.ForeignKey(Driver, on_delete=models.CASCADE)
    latitude = models.FloatField(default=0.0)
    longitude = models.FloatField(default=0.0)
    is_moving = models.BooleanField(default=False)
    last_updated = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"Bus - {self.route.name}"

# Create your models here.
