from .models import UserRoutePreference, StaleTrip
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from django.core.cache import cache
from django.utils import timezone
from django.db.models import Q
import math
from .models import EmergencyAlert, PassengerCountLog, StopArrival
from django.utils import timezone
from django.core.cache import cache
from .models import (BusLocation, PassengerWaiting, Subscription,
                     NotificationLog, Trip, LocationSharingSession,
                     DriverFrequentRoute)
from buses.models import Bus, Route, Stop, RouteStop

def success(data):
    return Response({'success': True, 'data': data})

def error(msg, status=400):
    return Response({'success': False, 'message': msg}, status=status)

def validate_coordinates(lat, lng):
    return 20 <= lat <= 40 and 60 <= lng <= 85

def calculate_distance(lat1, lng1, lat2, lng2):
    R = 6371000
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat/2)**2 +
         math.cos(math.radians(lat1)) *
         math.cos(math.radians(lat2)) *
         math.sin(dlng/2)**2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

def estimate_eta(bus_lat, bus_lng, stop_lat, stop_lng, speed_kmh=30):
    distance = calculate_distance(bus_lat, bus_lng, stop_lat, stop_lng)
    speed_ms = speed_kmh * 1000 / 3600
    eta_seconds = distance / speed_ms if speed_ms > 0 else 0
    return round(eta_seconds / 60)

def get_active_buses():
    now = timezone.now()
    active = Bus.objects.filter(
        is_active=True,
        location__isnull=False
    ).select_related('location', 'route', 'driver')

    result = []
    for bus in active:
        loc = bus.location
        diff = (now - loc.last_updated).total_seconds()
        if diff > 600:
            bus.is_active = False
            bus.save()
            continue
        if diff > 60:
            continue
        result.append(bus)
    return result

# ─── PASSENGER APIs ────────────────────────────────────────

@api_view(['GET'])
@permission_classes([AllowAny])
def get_buses(request):
    lat = request.query_params.get('lat')
    lng = request.query_params.get('lng')
    radius_m = float(request.query_params.get('radius_m', 5000))

    buses = get_active_buses()
    result = []

    for bus in buses:
        loc = bus.location
        bus_data = {
    'bus_id': bus.id,
    'plate': bus.plate_number,
    'route': bus.route.name,
    'route_id': bus.route.id,
    'start': bus.route.start_point,
    'end': bus.route.end_point,
    'lat': loc.lat,
    'lng': loc.lng,
    'driver_name': bus.driver.name if bus.driver else 'Unknown',
    'is_verified': bus.driver.is_approved if bus.driver else False,  # ← Blue tick
    'is_paused': False,
    'last_updated': loc.last_updated.isoformat(),
}

        if lat and lng:
            try:
                dist = calculate_distance(float(lat), float(lng), loc.lat, loc.lng)
                if dist > radius_m:
                    continue
                bus_data['distance_m'] = round(dist)
                bus_data['eta_minutes'] = estimate_eta(
                    loc.lat, loc.lng, float(lat), float(lng)
                )
            except (ValueError, TypeError):
                pass

        result.append(bus_data)  # ← YAHI MISSING THA

    return success({'buses': result, 'count': len(result)})
    
 
@api_view(['GET'])
@permission_classes([AllowAny])
def get_bus_detail(request, bus_id):
    try:
        bus = Bus.objects.select_related(
            'location', 'route', 'driver'
        ).get(id=bus_id, is_active=True)
    except Bus.DoesNotExist:
        return error('Bus not found', 404)

    try:
        loc = bus.location
    except BusLocation.DoesNotExist:
        return error('Location not available', 404)

    stops = RouteStop.objects.filter(
        route=bus.route
    ).select_related('stop').order_by('order')

    return success({
        'bus_id': bus.id,
        'plate': bus.plate_number,
        'route': bus.route.name,
        'start': bus.route.start_point,
        'end': bus.route.end_point,
        'driver_name': bus.driver.name if bus.driver else 'Unknown',
        'stops': [{
            'id': rs.stop.id,
            'name': rs.stop.name,
            'lat': rs.stop.lat,
            'lng': rs.stop.lng
        } for rs in stops],
        'lat': loc.lat,
        'lng': loc.lng,
        'last_updated': loc.last_updated.isoformat(),
    })
    
@api_view(['POST'])
@permission_classes([AllowAny])
def search_buses(request):
    from_stop_id = request.data.get('from_stop_id')
    to_stop_id = request.data.get('to_stop_id')
    user_lat = request.data.get('user_lat')
    user_lng = request.data.get('user_lng')

    if not from_stop_id or not to_stop_id:
        return error('from_stop_id and to_stop_id required')

    try:
        from_stop = Stop.objects.get(id=from_stop_id)
        to_stop = Stop.objects.get(id=to_stop_id)
    except Stop.DoesNotExist:
        return error('Stop not found', 404)

    from_routes = RouteStop.objects.filter(stop=from_stop).values_list('route_id', 'order')
    to_routes = RouteStop.objects.filter(stop=to_stop).values_list('route_id', 'order')

    from_dict = {r: o for r, o in from_routes}
    to_dict = {r: o for r, o in to_routes}

    valid_routes = []
    for route_id in from_dict:
        if route_id in to_dict and from_dict[route_id] < to_dict[route_id]:
            valid_routes.append(route_id)

    active_buses = get_active_buses()
    matched = []
    nearby = []

    for bus in active_buses:
        loc = bus.location
        bus_data = {
            'bus_id': bus.id,
            'plate': bus.plate_number,
            'route': bus.route.name,
            'lat': loc.lat,
            'lng': loc.lng,
        }

        if bus.route.id in valid_routes:
            if user_lat and user_lng:
                bus_data['eta_minutes'] = estimate_eta(
                    loc.lat, loc.lng, from_stop.lat, from_stop.lng
                )
            matched.append(bus_data)
        elif user_lat and user_lng:
            try:
                dist = calculate_distance(float(user_lat), float(user_lng), loc.lat, loc.lng)
                if dist <= 5000:
                    bus_data['distance_m'] = round(dist)
                    nearby.append(bus_data)
            except (ValueError, TypeError):
                pass

    return success({
        'matched_buses': matched,
        'nearby_buses': nearby,
        'from_stop': {'id': from_stop.id, 'name': from_stop.name},
        'to_stop': {'id': to_stop.id, 'name': to_stop.name},
    })

@api_view(['GET'])
@permission_classes([AllowAny])
def stops_autocomplete(request):
    q = request.query_params.get('q', '').strip()
    if not q:
        return error('Query required')

    stops = Stop.objects.filter(name__icontains=q)[:10]
    return success({
        'stops': [{'id': s.id, 'name': s.name, 'lat': s.lat, 'lng': s.lng} for s in stops]
    })

@api_view(['GET'])
@permission_classes([AllowAny])
def get_routes(request):
    routes = Route.objects.all()
    return success({
        'routes': [{'id': r.id, 'name': r.name, 'start': r.start_point, 'end': r.end_point} for r in routes]
    })

# ─── LOCATION SHARING ──────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def share_location_start(request):
    from_stop_id = request.data.get('from_stop_id')
    to_stop_id = request.data.get('to_stop_id')
    lat = request.data.get('lat')
    lng = request.data.get('lng')

    if not all([from_stop_id, to_stop_id, lat, lng]):
        return error('from_stop_id, to_stop_id, lat, lng required')

    try:
        from_stop = Stop.objects.get(id=from_stop_id)
        to_stop = Stop.objects.get(id=to_stop_id)
    except Stop.DoesNotExist:
        return error('Stop not found', 404)

    LocationSharingSession.objects.filter(user=request.user, is_active=True).update(is_active=False)

    session = LocationSharingSession.objects.create(
        user=request.user,
        from_stop=from_stop,
        to_stop=to_stop,
        lat=float(lat),
        lng=float(lng),
    )

    return success({'session_id': session.id})

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def share_location_update(request):
    session_id = request.data.get('session_id')
    lat = request.data.get('lat')
    lng = request.data.get('lng')

    try:
        session = LocationSharingSession.objects.get(id=session_id, user=request.user, is_active=True)
    except LocationSharingSession.DoesNotExist:
        return error('Session not found', 404)

    session.lat = float(lat)
    session.lng = float(lng)
    session.save()

    return success({'message': 'Location updated'})

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def share_location_stop(request):
    session_id = request.data.get('session_id')

    try:
        session = LocationSharingSession.objects.get(id=session_id, user=request.user)
        session.is_active = False
        session.save()
        return success({'message': 'Sharing stopped'})
    except LocationSharingSession.DoesNotExist:
        return error('Session not found', 404)

# ─── WAITING ───────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def mark_waiting(request):
    user = request.user
    route_id = request.data.get('route_id')
    lat = request.data.get('lat')
    lng = request.data.get('lng')
    from_stop_id = request.data.get('from_stop_id')
    to_stop_id = request.data.get('to_stop_id')

    if not all([route_id, lat, lng]):
        return error('route_id, lat, lng required')

    try:
        route = Route.objects.get(id=route_id)
    except Route.DoesNotExist:
        return error('Route not found', 404)

    PassengerWaiting.objects.filter(user=user, got_bus=False).delete()

    waiting = PassengerWaiting.objects.create(
        user=user,
        route=route,
        lat=float(lat),
        lng=float(lng),
        from_stop_id=from_stop_id,
        to_stop_id=to_stop_id,
    )

    return success({'message': 'Marked as waiting', 'id': waiting.id})

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def got_bus(request):
    PassengerWaiting.objects.filter(user=request.user, got_bus=False).update(got_bus=True)
    return success({'message': 'Marked as got bus'})

@api_view(['GET'])
@permission_classes([AllowAny])
def get_waiting_passengers(request, route_id):
    waiting = PassengerWaiting.objects.filter(
        route_id=route_id, got_bus=False
    ).values('lat', 'lng', 'user__name', 'from_stop__name', 'to_stop__name')
    return success({'passengers': list(waiting)})

# ─── SUBSCRIBE ─────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def subscribe_route(request):
    route_id = request.data.get('route_id')
    time_window = request.data.get('time_window', '').upper()
    from_stop_id = request.data.get('from_stop_id')
    to_stop_id = request.data.get('to_stop_id')

    if not route_id or time_window not in ['AM', 'PM']:
        return error('route_id and time_window (AM/PM) required')

    try:
        route = Route.objects.get(id=route_id)
    except Route.DoesNotExist:
        return error('Route not found', 404)

    sub, created = Subscription.objects.get_or_create(
        user=request.user,
        route=route,
        time_window=time_window,
        defaults={
            'is_active': True,
            'from_stop_id': from_stop_id,
            'to_stop_id': to_stop_id,
        }
    )

    if not created:
        sub.is_active = True
        sub.save()

    return success({'message': f'Subscribed to {route.name} ({time_window})', 'id': sub.id})

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_subscriptions(request):
    subs = Subscription.objects.filter(user=request.user, is_active=True).select_related('route')
    return success({
        'subscriptions': [{
            'id': s.id,
            'route': s.route.name,
            'time_window': s.time_window,
        } for s in subs]
    })

@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def unsubscribe_route(request, sub_id):
    try:
        sub = Subscription.objects.get(id=sub_id, user=request.user)
        sub.is_active = False
        sub.save()
        return success({'message': 'Unsubscribed'})
    except Subscription.DoesNotExist:
        return error('Subscription not found', 404)

# ─── DRIVER APIs ───────────────────────────────────────────
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def driver_profile(request):
    user = request.user

    if not user.is_driver:
        return error('Not a driver')

    # Bus dhundho — user.bus se ya bus_number se
    bus = None
    try:
        bus = user.bus
    except Exception:
        pass

    # Agar user.bus nahi mila toh bus_number se dhundho
    if not bus and user.bus_number:
        from buses.models import Bus as BusModel
        try:
            bus = BusModel.objects.get(plate_number=user.bus_number)
            # Assign kar do
            bus.driver = user
            bus.save()
        except BusModel.DoesNotExist:
            # Bus exist nahi karta — banao
            from buses.models import Route
            route = Route.objects.first()
            if route:
                bus = BusModel.objects.create(
                    plate_number=user.bus_number,
                    route=route,
                    driver=user,
                    is_active=False,
                )

    if not bus:
        return error('No bus assigned. Contact admin.')

    active_trip = Trip.objects.filter(
        driver=user, status__in=['active', 'paused']
    ).first()

    return success({
        'name': user.name,
        'phone': user.phone,
        'bus_number': bus.plate_number,
        'license_no': user.license_no,
        'is_approved': user.is_approved,
        'is_verified': user.is_approved,
        'trip_status': active_trip.status if active_trip else 'inactive',
        'trip_id': active_trip.id if active_trip else None,
        'route': {
            'id': bus.route.id,
            'name': bus.route.name,
            'start': bus.route.start_point,
            'end': bus.route.end_point,
        }
    })

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def driver_routes(request):
    user = request.user
    if not user.is_driver:
        return error('Not a driver')

    try:
        bus = user.bus
        frequent = DriverFrequentRoute.objects.filter(driver=user).values_list('route_id', flat=True)
        route = bus.route
        return success({
    'name': user.name,
    'phone': user.phone,
    'bus_number': bus.plate_number,
    'license_no': user.license_no,
    'is_approved': user.is_approved,
    'is_verified': user.is_approved,  # Blue tick
    'trip_status': active_trip.status if active_trip else 'inactive',
    'trip_id': active_trip.id if active_trip else None,
    'route': {
        'id': bus.route.id,
        'name': bus.route.name,
        'start': bus.route.start_point,
        'end': bus.route.end_point,
    }
})
    except Bus.DoesNotExist:
        return error('No bus assigned')

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def start_trip(request):
    user = request.user
    if not user.is_driver:
        return error('Not a driver')
    if not user.is_approved:
        return error('Driver not approved')

    try:
        bus = user.bus
    except Bus.DoesNotExist:
        return error('No bus assigned')

    lat = request.data.get('lat', 0)
    lng = request.data.get('lng', 0)

    Trip.objects.filter(driver=user, status='active').update(status='cancelled')

    trip = Trip.objects.create(
        driver=user,
        bus=bus,
        route=bus.route,
        status='active'
    )

    bus.is_active = True
    bus.save()

    BusLocation.objects.update_or_create(
        bus=bus,
        defaults={'lat': float(lat), 'lng': float(lng), 'trip': trip}
    )

    cache_data = {
        'bus_id': bus.id,
        'lat': float(lat),
        'lng': float(lng),
        'route': bus.route.name,
        'plate': bus.plate_number,
        'last_updated': timezone.now().isoformat(),
    }
    cache.set(f'bus_location_{bus.id}', cache_data, timeout=60)

    return success({'message': 'Trip started', 'bus_id': bus.id, 'trip_id': trip.id})

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def update_location(request):
    user = request.user
    if not user.is_driver:
        return error('Not a driver')
    if not user.is_approved:
        return error('Driver not approved')

    try:
        bus = user.bus
    except Bus.DoesNotExist:
        return error('No bus assigned')

    if not bus.is_active:
        return error('Start trip first')

    lat = request.data.get('lat')
    lng = request.data.get('lng')
    speed = float(request.data.get('speed', 0))

    if lat is None or lng is None:
        return error('lat and lng required')

    try:
        lat = float(lat)
        lng = float(lng)
    except (ValueError, TypeError):
        return error('Invalid coordinates')

    if not validate_coordinates(lat, lng):
        return error('Coordinates out of bounds')

    active_trip = Trip.objects.filter(driver=user, status='active').first()

    BusLocation.objects.update_or_create(
        bus=bus,
        defaults={'lat': lat, 'lng': lng, 'speed': speed, 'trip': active_trip}
    )

    cache_data = {
        'bus_id': bus.id,
        'lat': lat,
        'lng': lng,
        'speed': speed,
        'route': bus.route.name,
        'plate': bus.plate_number,
        'last_updated': timezone.now().isoformat(),
    }
    cache.set(f'bus_location_{bus.id}', cache_data, timeout=60)

    return success({'message': 'Location updated'})

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def pause_trip(request):
    user = request.user
    action = request.data.get('action', 'pause')
    trip_id = request.data.get('trip_id')

    try:
        trip = Trip.objects.get(id=trip_id, driver=user)
    except Trip.DoesNotExist:
        return error('Trip not found', 404)

    if action == 'pause':
        trip.is_paused = True
        trip.status = 'paused'
    else:
        trip.is_paused = False
        trip.status = 'active'

    trip.save()
    return success({'message': f'Trip {action}d', 'is_paused': trip.is_paused})

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def end_trip(request):
    user = request.user
    if not user.is_driver:
        return error('Not a driver')

    try:
        bus = user.bus
    except Bus.DoesNotExist:
        return error('No bus assigned')

    trip = Trip.objects.filter(driver=user, status__in=['active', 'paused']).first()
    if trip:
        trip.status = 'completed'
        trip.end_time = timezone.now()
        trip.save()

        mark_frequent = request.data.get('mark_frequent', False)
        if mark_frequent:
            freq, _ = DriverFrequentRoute.objects.get_or_create(driver=user, route=trip.route)
            freq.usage_count += 1
            freq.save()

    bus.is_active = False
    bus.save()
    cache.delete(f'bus_location_{bus.id}')

    return success({'message': 'Trip ended'})

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_trip_passengers(request):
    trip_id = request.query_params.get('trip_id')

    try:
        trip = Trip.objects.get(id=trip_id, driver=request.user)
    except Trip.DoesNotExist:
        return error('Trip not found', 404)

    route_stops = RouteStop.objects.filter(route=trip.route).values_list('stop_id', flat=True)

    sessions = LocationSharingSession.objects.filter(
        is_active=True,
        from_stop_id__in=route_stops,
        to_stop_id__in=route_stops,
    ).select_related('user', 'from_stop', 'to_stop')

    return success({
        'passengers': [{
            'user_id': s.user.id,
            'name': s.user.name,
            'lat': s.lat,
            'lng': s.lng,
            'from_stop': s.from_stop.name,
            'to_stop': s.to_stop.name,
        } for s in sessions]
    })

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def trip_summary(request):
    trip_id = request.query_params.get('trip_id')

    try:
        trip = Trip.objects.get(id=trip_id, driver=request.user)
    except Trip.DoesNotExist:
        return error('Trip not found', 404)

    duration = None
    if trip.end_time:
        duration = round((trip.end_time - trip.start_time).total_seconds() / 60)

    return success({
        'trip_id': trip.id,
        'route': trip.route.name,
        'status': trip.status,
        'start_time': trip.start_time.isoformat(),
        'end_time': trip.end_time.isoformat() if trip.end_time else None,
        'duration_minutes': duration,
        'passenger_count': trip.passenger_count,
    })
# ─── PHASE 2 — USER PREFERENCES ───────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def save_route_preference(request):
    from_stop_id = request.data.get('from_stop_id')
    to_stop_id = request.data.get('to_stop_id')

    if not from_stop_id or not to_stop_id:
        return error('from_stop_id and to_stop_id required')

    try:
        from_stop = Stop.objects.get(id=from_stop_id)
        to_stop = Stop.objects.get(id=to_stop_id)
    except Stop.DoesNotExist:
        return error('Stop not found', 404)

    pref, _ = UserRoutePreference.objects.update_or_create(
        user=request.user,
        defaults={'from_stop': from_stop, 'to_stop': to_stop}
    )

    return success({
        'message': 'Preference saved',
        'from_stop': from_stop.name,
        'to_stop': to_stop.name,
    })

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_route_preference(request):
    try:
        pref = request.user.route_preference
        return success({
            'from_stop': {'id': pref.from_stop.id, 'name': pref.from_stop.name},
            'to_stop': {'id': pref.to_stop.id, 'name': pref.to_stop.name},
        })
    except UserRoutePreference.DoesNotExist:
        return success({'from_stop': None, 'to_stop': None})

# ─── PHASE 2 — STALE TRIP DETECTION ───────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def force_refresh(request):
    trip_id = request.data.get('trip_id')
    try:
        trip = Trip.objects.get(id=trip_id, driver=request.user)
        return success({'message': 'Ping received', 'trip_id': trip.id})
    except Trip.DoesNotExist:
        return error('Trip not found', 404)

def check_stale_trips():
    now = timezone.now()
    active_trips = Trip.objects.filter(status='active')
    for trip in active_trips:
        try:
            loc = trip.bus.location
            diff = (now - loc.last_updated).total_seconds()
            if diff > 30:
                trip.status = 'stale'
                trip.save()
                trip.bus.is_active = False
                trip.bus.save()
        except BusLocation.DoesNotExist:
            pass

# ─── PHASE 2 — SUBSCRIBE WITH DAILY TIME ──────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def subscribe_with_time(request):
    route_id = request.data.get('route_id')
    from_stop_id = request.data.get('from_stop_id')
    to_stop_id = request.data.get('to_stop_id')
    daily_time = request.data.get('daily_time')
    time_window = request.data.get('time_window', 'AM').upper()

    if not route_id or not daily_time:
        return error('route_id and daily_time required')

    try:
        from datetime import datetime
        time_obj = datetime.strptime(daily_time, '%H:%M').time()
    except ValueError:
        return error('daily_time format must be HH:MM')

    try:
        route = Route.objects.get(id=route_id)
    except Route.DoesNotExist:
        return error('Route not found', 404)

    sub, created = Subscription.objects.update_or_create(
        user=request.user,
        route=route,
        time_window=time_window,
        defaults={
            'from_stop_id': from_stop_id,
            'to_stop_id': to_stop_id,
            'daily_time': time_obj,
            'is_active': True,
        }
    )

    return success({
        'message': f'Subscribed to {route.name} at {daily_time}',
        'id': sub.id,
        'daily_time': daily_time,
    })

# ─── PHASE 3 — NOTIFICATION TRIGGER ──────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def trigger_notifications(request):
    bus_id = request.data.get('bus_id')
    if not bus_id:
        return error('bus_id required')

    try:
        bus = Bus.objects.select_related('route').get(id=bus_id)
    except Bus.DoesNotExist:
        return error('Bus not found', 404)

    from .firebase import send_bulk_notification
    now = timezone.now()
    hour = now.hour
    current_window = 'AM' if 5 <= hour <= 12 else 'PM'

    subs = Subscription.objects.filter(
        route=bus.route,
        time_window=current_window,
        is_active=True,
        user__fcm_token__isnull=False
    ).exclude(
        user__in=NotificationLog.objects.filter(
            route=bus.route,
            bus=bus,
            time_window=current_window,
            sent_at__date=now.date()
        ).values('user')
    ).select_related('user')

    tokens = []
    notified = []

    for sub in subs:
        if sub.user.fcm_token:
            tokens.append(sub.user.fcm_token)
            notified.append(sub)

    if tokens:
        send_bulk_notification(
            tokens=tokens,
            title=f'🚌 Bus Coming — {bus.route.name}',
            body='Your bus is on the way! Open KTP to track.',
            data={'bus_id': str(bus.id), 'route_id': str(bus.route.id)}
        )
        for sub in notified:
            NotificationLog.objects.get_or_create(
                user=sub.user,
                route=bus.route,
                bus=bus,
                time_window=current_window,
            )

    return success({'message': f'Notified {len(tokens)} users'})

# ─── PHASE 3 — DRIVER VERIFIED BADGE ─────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def driver_badge(request):
    user = request.user
    if not user.is_driver:
        return error('Not a driver')
    return success({
        'is_verified': user.is_approved,
        'badge': 'verified' if user.is_approved else 'pending',
        'badge_color': '#2196F3' if user.is_approved else '#9E9E9E',
    })

# ─── PHASE 3 — TRIP COMPRESSED SUMMARY ───────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def trip_history(request):
    trips = Trip.objects.filter(
        driver=request.user,
        status='completed'
    ).order_by('-start_time')[:10]

    return success({
        'trips': [{
            'trip_id': t.id,
            'route': t.route.name,
            'date': t.start_time.strftime('%Y-%m-%d'),
            'duration_minutes': round(
                (t.end_time - t.start_time).total_seconds() / 60
            ) if t.end_time else None,
            'passenger_count': t.passenger_count,
        } for t in trips]
    })

# ─── PHASE 3 — ADMIN STATS ────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def admin_stats(request):
    if not request.user.is_staff:
        return error('Admin only', 403)

    from users.models import User
    return success({
        'total_users': User.objects.count(),
        'total_drivers': User.objects.filter(is_driver=True).count(),
        'approved_drivers': User.objects.filter(is_driver=True, is_approved=True).count(),
        'active_buses': Bus.objects.filter(is_active=True).count(),
        'total_trips_today': Trip.objects.filter(
            start_time__date=timezone.now().date()
        ).count(),
        'active_sharing_sessions': LocationSharingSession.objects.filter(is_active=True).count(),
    })
# ─── EMERGENCY ALERT ───────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def emergency_alert(request):
    user = request.user

    if not user.is_driver:
        return error('Not a driver')

    # Rate limit — 1 emergency per 5 minutes
    cache_key = f'emergency_{user.id}'
    if cache.get(cache_key):
        return error('Emergency already sent. Wait 5 minutes.', 429)

    trip_id = request.data.get('trip_id')

    if trip_id:
        try:
            trip = Trip.objects.get(id=trip_id, driver=user, status__in=['active', 'paused'])
        except Trip.DoesNotExist:
            return error('Active trip not found', 404)
    else:
        trip = Trip.objects.filter(driver=user, status__in=['active', 'paused']).first()
        if not trip:
            return error('No active trip found', 404)

    try:
        loc = trip.bus.location
        lat = loc.lat
        lng = loc.lng
    except BusLocation.DoesNotExist:
        lat = 0.0
        lng = 0.0

    alert = EmergencyAlert.objects.create(
        driver=user,
        trip=trip,
        latitude=lat,
        longitude=lng,
    )

    # Rate limit set karo
    cache.set(cache_key, True, timeout=300)

    # Admin ko notify karo
    print(f"🚨 EMERGENCY ALERT — Driver: {user.name} | Trip: {trip.id} | Location: {lat},{lng}")

    # FCM notification to admins
    from users.models import User as UserModel
    admin_tokens = list(
        UserModel.objects.filter(
            is_staff=True,
            fcm_token__isnull=False
        ).values_list('fcm_token', flat=True)
    )

    if admin_tokens:
        try:
            from .firebase import send_bulk_notification
            send_bulk_notification(
                tokens=admin_tokens,
                title='🚨 Emergency Alert!',
                body=f'Driver {user.name} needs help! Trip #{trip.id}',
                data={
                    'alert_id': str(alert.id),
                    'driver': user.name,
                    'lat': str(lat),
                    'lng': str(lng),
                    'type': 'emergency',
                }
            )
        except Exception as e:
            print(f"FCM Error: {e}")

    return success({
        'alert_id': alert.id,
        'message': 'Emergency alert sent',
        'location': {'lat': lat, 'lng': lng},
    })

# ─── PASSENGER COUNT UPDATE ────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def update_passenger_count(request):
    user = request.user

    if not user.is_driver:
        return error('Not a driver')

    trip_id = request.data.get('trip_id')
    count = request.data.get('count')

    if count is None:
        return error('count required')

    try:
        count = int(count)
        if count < 0:
            return error('count cannot be negative')
    except (ValueError, TypeError):
        return error('count must be a number')

    try:
        trip = Trip.objects.get(id=trip_id, driver=user, status__in=['active', 'paused'])
    except Trip.DoesNotExist:
        return error('Active trip not found', 404)

    trip.passenger_count = count
    trip.save()

    # Log karo for analytics
    PassengerCountLog.objects.create(trip=trip, count=count)

    return success({
        'count': count,
        'trip_id': trip.id,
        'message': 'Passenger count updated',
    })

# ─── ARRIVAL CONFIRMATION ──────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def confirm_arrival(request):
    user = request.user

    if not user.is_driver:
        return error('Not a driver')

    trip_id = request.data.get('trip_id')
    stop_id = request.data.get('stop_id')

    if not trip_id or not stop_id:
        return error('trip_id and stop_id required')

    try:
        trip = Trip.objects.select_related('route').get(
            id=trip_id, driver=user, status__in=['active', 'paused']
        )
    except Trip.DoesNotExist:
        return error('Active trip not found', 404)

    try:
        stop = Stop.objects.get(id=stop_id)
    except Stop.DoesNotExist:
        return error('Stop not found', 404)

    # Stop belongs to route check
    if not RouteStop.objects.filter(route=trip.route, stop=stop).exists():
        return error('Stop not on this route', 400)

    # Arrival mark karo
    arrival, created = StopArrival.objects.get_or_create(trip=trip, stop=stop)

    if not created:
        return error('Already confirmed arrival at this stop', 400)

    # Next stops calculate karo
    current_order = RouteStop.objects.get(route=trip.route, stop=stop).order
    next_route_stops = RouteStop.objects.filter(
        route=trip.route,
        order__gt=current_order
    ).select_related('stop').order_by('order')[:3]

    next_stops = []
    for rs in next_route_stops:
        eta = None
        try:
            loc = trip.bus.location
            eta = estimate_eta(loc.lat, loc.lng, rs.stop.lat, rs.stop.lng)
        except Exception:
            pass

        next_stops.append({
            'stop_id': rs.stop.id,
            'name': rs.stop.name,
            'lat': rs.stop.lat,
            'lng': rs.stop.lng,
            'order': rs.order,
            'eta_minutes': eta,
        })

    # Notify subscribers at this stop
    subs = Subscription.objects.filter(
        from_stop=stop,
        is_active=True,
        user__fcm_token__isnull=False
    ).select_related('user')

    tokens = [s.user.fcm_token for s in subs if s.user.fcm_token]
    if tokens:
        try:
            from .firebase import send_bulk_notification
            send_bulk_notification(
                tokens=tokens,
                title=f'🚌 Bus arriving at {stop.name}',
                body=f'Your bus on {trip.route.name} is here!',
                data={'stop_id': str(stop.id), 'trip_id': str(trip.id)}
            )
        except Exception as e:
            print(f"FCM Error: {e}")

    return success({
        'message': f'Arrival confirmed at {stop.name}',
        'stop': {'id': stop.id, 'name': stop.name},
        'next_stops': next_stops,
    })

# ─── NEXT STOPS FOR TRIP ───────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def next_stops(request):
    user = request.user

    if not user.is_driver:
        return error('Not a driver')

    trip_id = request.query_params.get('trip_id')

    try:
        trip = Trip.objects.select_related('route').get(
            id=trip_id, driver=user
        )
    except Trip.DoesNotExist:
        return error('Trip not found', 404)

    route_stops = RouteStop.objects.filter(
        route=trip.route
    ).select_related('stop').order_by('order')

    arrived_stops = set(
        StopArrival.objects.filter(trip=trip).values_list('stop_id', flat=True)
    )

    last_arrived_order = 0
    for rs in route_stops:
        if rs.stop.id in arrived_stops:
            last_arrived_order = rs.order

    result = []
    for rs in route_stops:
        if rs.stop.id in arrived_stops:
            status = 'arrived'
        elif rs.order == last_arrived_order + 1:
            status = 'current'
        elif rs.order > last_arrived_order:
            status = 'upcoming'
        else:
            status = 'arrived'

        eta = None
        if status in ['current', 'upcoming']:
            try:
                loc = trip.bus.location
                eta = estimate_eta(loc.lat, loc.lng, rs.stop.lat, rs.stop.lng)
            except Exception:
                pass

        result.append({
            'stop_id': rs.stop.id,
            'name': rs.stop.name,
            'lat': rs.stop.lat,
            'lng': rs.stop.lng,
            'order': rs.order,
            'status': status,
            'eta_minutes': eta,
        })

    return success({'stops': result})

# ─── RESOLVE EMERGENCY ─────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def resolve_emergency(request):
    if not request.user.is_staff:
        return error('Admin only', 403)

    alert_id = request.data.get('alert_id')
    try:
        alert = EmergencyAlert.objects.get(id=alert_id)
        alert.resolved = True
        alert.notes = request.data.get('notes', '')
        alert.save()
        return success({'message': 'Emergency resolved'})
    except EmergencyAlert.DoesNotExist:
        return error('Alert not found', 404)