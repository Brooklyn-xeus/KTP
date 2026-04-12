from django.urls import path
from . import views

urlpatterns = [
    # Driver
    path('driver/start-trip/', views.start_trip),
    path('driver/update-location/', views.update_location),
    path('driver/end-trip/', views.end_trip),

    # Passenger
    path('buses/', views.get_buses),
    path('buses/<int:bus_id>/', views.get_bus_detail),

    # Waiting
    path('passenger/waiting/', views.mark_waiting),
    path('passenger/got-bus/', views.got_bus),
    path('passenger/waiting/<int:route_id>/', views.get_waiting_passengers),

    # Subscribe
    path('subscribe/', views.subscribe_route),
    path('subscribe/my/', views.my_subscriptions),
    path('subscribe/<int:sub_id>/delete/', views.unsubscribe_route),

    # Notifications
    path('notify/trigger/', views.trigger_notifications),
]

