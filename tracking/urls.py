from django.urls import path
from . import views

urlpatterns = [
    # Passenger
    path('buses/', views.get_buses),
    path('buses/<int:bus_id>/', views.get_bus_detail),
    path('search/', views.search_buses),
    path('stops/autocomplete/', views.stops_autocomplete),
    path('routes/', views.get_routes),

    # Location sharing
    path('share/start/', views.share_location_start),
    path('share/update/', views.share_location_update),
    path('share/stop/', views.share_location_stop),

    # Waiting
    path('passenger/waiting/', views.mark_waiting),
    path('passenger/got-bus/', views.got_bus),
    path('passenger/waiting/<int:route_id>/', views.get_waiting_passengers),

    # Subscribe
    path('subscribe/', views.subscribe_route),
    path('subscribe/my/', views.my_subscriptions),
    path('subscribe/<int:sub_id>/delete/', views.unsubscribe_route),

    # Driver
    path('driver/profile/', views.driver_profile),
    path('driver/routes/', views.driver_routes),
    path('driver/start-trip/', views.start_trip),
    path('driver/update-location/', views.update_location),
    path('driver/pause-trip/', views.pause_trip),
    path('driver/end-trip/', views.end_trip),
    path('driver/passengers/', views.get_trip_passengers),
    path('driver/trip-summary/', views.trip_summary),
]
