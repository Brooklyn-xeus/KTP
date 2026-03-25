from django.urls import path
from . import views

urlpatterns = [
    path('bus-location/', views.bus_location, name='bus_location'),
]