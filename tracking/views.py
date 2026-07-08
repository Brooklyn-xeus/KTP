# views.py - Fully corrected version
import math
from datetime import datetime

from django.core.cache import cache
from django.db import connection
from django.db.models import Q
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response

from .firebase import send_bulk_notification
from .models import (
    EmergencyAlert, PassengerCountLog, StopArrival, UserRoutePreference,
    StaleTrip, BusLocation, PassengerWaiting, Subscription, NotificationLog,
    Trip, LocationSharingSession, DriverFrequentRoute
)
from buses.models import Bus, Route, Stop, RouteStop
from users.models import User as UserModel


# ========== HELPERS ==========
def success(data):
    return Response({'success': True, 'data': data})

def error(msg, status=400):
    return Response({'success': False, 'message': msg}, status=status)

def validate_coordinates(lat, lng):
    # Kashmir bounds (approx)
    return 32.5 <= float(lat) <= 35.5 and 73.5 <= float(lng) <= 80.5

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

def mask_coordinates(lat, lng, precision=0.001):
    masked_lat = round(round(lat / precision) * precision, 6)
    masked_lng = round(round(lng / precision) * precision, 6)
    return masked_lat, masked_lng

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

def detect_gps_jump(prev_lat, prev_lng, new_lat, new_lng, seconds_elapsed):
    if not all([prev_lat, prev_lng]):
        return False
    distance = calculate_distance(prev_lat, prev_lng, new_lat, new_lng)
    max_distance = (80 * 1000 / 3600) * seconds_elapsed * 2
    return distance > max_distance and distance > 500

def check_waiting_spam(user_id):
    key = f'waiting_spam_{user_id}'
    count = cache.get(key, 0)
    if count >= 10:
        return True
    cache.set(key, count + 1, timeout=3600)
    return False

def log_suspicious(user, action, detail):
    print(f"🚨 SUSPICIOUS: User {user.id} ({user.name}) — {action}: {detail}")


# ========== PASSENGER APIs ==========
@api_view(['GET'])
@permission_classes([AllowAny])
def get_buses(request):
    lat = request.query_params.get('lat')
    lng = request.query_params.get('lng')
    radius_m = float(request.query_params.get('radius_m', 5000))
    vehicle_type_filter = request.query_params.get('vehicle_type')

    buses = get_active_buses()
    result = []

    for bus in buses:
        if vehicle_type_filter and bus.vehicle_type != vehicle_type_filter:
            continue

        loc = bus.location
        bus_data = {
            'bus_id': bus.id,
            'plate': bus.plate_number,
            'vehicle_type': bus.vehicle_type,
            'icon': '🚌' if bus.vehicle_type == 'bus' else '🚐',
            'route': bus.route.name,
            'route_id': bus.route.id,
            'start': bus.route.start_point,
            'end': bus.route.end_point,
            'lat': loc.lat,
            'lng': loc.lng,
            'driver_name': bus.driver.name if bus.driver else 'Unknown',
            'is_verified': bus.driver.is_approved if bus.driver else False,
            'is_paused': False,
            'last_updated': loc.last_updated.isoformat(),
        }

        if lat and lng:
            try:
                dist = calculate_distance(float(lat), float(lng), loc.lat, loc.lng)
                if dist > radius_m:
                    continue
                bus_data['distance_m'] = round(dist)
                bus_data['eta_minutes'] = estimate_eta(loc.lat, loc.lng, float(lat), float(lng))
            except (ValueError, TypeError):
                pass

        result.append(bus_data)

    return success({'buses': result, 'count': len(result)})

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

    from_dict = dict(from_routes)
    to_dict = dict(to_routes)

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
                bus_data['eta_minutes'] = estimate_eta(loc.lat, loc.lng, from_stop.lat, from_stop.lng)
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

    cache_key = f'stops_{q.lower()}'
    cached = cache.get(cache_key)
    if cached:
        return success(cached)

    stops = Stop.objects.filter(name__icontains=q)[:10]
    data = {
        'stops': [{'id': s.id, 'name': s.name, 'lat': s.lat, 'lng': s.lng} for s in stops]
    }
    cache.set(cache_key, data, timeout=1800)
    return success(data)

@api_view(['GET'])
@permission_classes([AllowAny])
def get_routes(request):
    cached = cache.get('all_routes')
    if cached:
        return success(cached)

    routes = Route.objects.all()
    data = {
        'routes': [{'id': r.id, 'name': r.name, 'start': r.start_point, 'end': r.end_point} for r in routes]
    }
    cache.set('all_routes', data, timeout=3600)
    return success(data)


# ========== LOCATION SHARING ==========
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


# ========== WAITING ==========
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
@permission_classes([IsAuthenticated])
def get_waiting_passengers(request, route_id):
    waiting = PassengerWaiting.objects.filter(
        route_id=route_id, got_bus=False
    ).values('lat', 'lng', 'from_stop__name', 'to_stop__name')

    masked = []
    for p in waiting:
        mlat, mlng = mask_coordinates(p['lat'], p['lng'])
        masked.append({
            'lat': mlat,
            'lng': mlng,
            'from_stop': p['from_stop__name'],
            'to_stop': p['to_stop__name'],
        })
    return success({'passengers': masked})


# ========== SUBSCRIPTIONS ==========
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


# ========== DRIVER APIs ==========
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def driver_profile(request):
    user = request.user
    if not user.is_driver:
        return error('Not a driver')

    bus = None
    try:
        bus = user.bus
    except Exception:
        pass

    if not bus and user.bus_number:
        from buses.models import Bus as BusModel
        try:
            bus = BusModel.objects.get(plate_number=user.bus_number)
            bus.driver = user
            bus.save()
        except BusModel.DoesNotExist:
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

    active_trip = Trip.objects.filter(driver=user, status__in=['active', 'paused']).first()

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
    """Return the driver's assigned route (no copy-paste mistakes)"""
    user = request.user
    if not user.is_driver:
        return error('Not a driver')

    try:
        bus = user.bus
        return success({
            'route_id': bus.route.id,
            'route_name': bus.route.name,
            'start': bus.route.start_point,
            'end': bus.route.end_point,
            'stops': list(bus.route.stops.values('id', 'name', 'lat', 'lng'))
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


# ========== PHASE 2 – USER PREFERENCES ==========
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


# ========== PHASE 2 – STALE TRIP ==========
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def force_refresh(request):
    trip_id = request.data.get('trip_id')
    try:
        trip = Trip.objects.get(id=trip_id, driver=request.user)
        return success({'message': 'Ping received', 'trip_id': trip.id})
    except Trip.DoesNotExist:
        return error('Trip not found', 404)


# ========== PHASE 2 – SUBSCRIBE WITH TIME ==========
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


# ========== PHASE 3 – NOTIFICATIONS ==========
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


# ========== PHASE 3 – DRIVER BADGE ==========
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


# ========== PHASE 3 – TRIP HISTORY ==========
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


# ========== PHASE 3 – ADMIN STATS ==========
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def admin_stats(request):
    if not request.user.is_staff:
        return error('Admin only', 403)

    return success({
        'total_users': UserModel.objects.count(),
        'total_drivers': UserModel.objects.filter(is_driver=True).count(),
        'approved_drivers': UserModel.objects.filter(is_driver=True, is_approved=True).count(),
        'active_buses': Bus.objects.filter(is_active=True).count(),
        'total_trips_today': Trip.objects.filter(
            start_time__date=timezone.now().date()
        ).count(),
        'active_sharing_sessions': LocationSharingSession.objects.filter(is_active=True).count(),
    })


# ========== EMERGENCY ALERT ==========
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def emergency_alert(request):
    user = request.user
    if not user.is_driver:
        return error('Not a driver')

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

    cache.set(cache_key, True, timeout=300)
    print(f"🚨 EMERGENCY ALERT — Driver: {user.name} | Trip: {trip.id} | Location: {lat},{lng}")

    admin_tokens = list(
        UserModel.objects.filter(
            is_staff=True,
            fcm_token__isnull=False
        ).values_list('fcm_token', flat=True)
    )

    if admin_tokens:
        try:
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


# ========== PASSENGER COUNT ==========
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
    PassengerCountLog.objects.create(trip=trip, count=count)

    return success({'count': count, 'trip_id': trip.id, 'message': 'Passenger count updated'})


# ========== ARRIVAL CONFIRMATION ==========
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

    if not RouteStop.objects.filter(route=trip.route, stop=stop).exists():
        return error('Stop not on this route', 400)

    arrival, created = StopArrival.objects.get_or_create(trip=trip, stop=stop)
    if not created:
        return error('Already confirmed arrival at this stop', 400)

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

    subs = Subscription.objects.filter(
        from_stop=stop,
        is_active=True,
        user__fcm_token__isnull=False
    ).select_related('user')

    tokens = [s.user.fcm_token for s in subs if s.user.fcm_token]
    if tokens:
        try:
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


# ========== NEXT STOPS ==========
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def next_stops(request):
    user = request.user
    if not user.is_driver:
        return error('Not a driver')

    trip_id = request.query_params.get('trip_id')
    try:
        trip = Trip.objects.select_related('route', 'bus').get(id=trip_id, driver=user)
    except Trip.DoesNotExist:
        return error('Trip not found', 404)

    route_stops = RouteStop.objects.filter(route=trip.route).select_related('stop').order_by('order')
    arrived_stops = set(StopArrival.objects.filter(trip=trip).values_list('stop_id', flat=True))

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


# ========== RESOLVE EMERGENCY (Admin) ==========
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


# ========== TRIP HISTORIES (Driver & Passenger) ==========
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def driver_trip_history(request):
    user = request.user
    if not user.is_driver:
        return error('Not a driver')

    trips = Trip.objects.filter(driver=user).order_by('-start_time')[:20]
    return success({
        'trips': [{
            'trip_id': t.id,
            'route': t.route.name,
            'status': t.status,
            'start_time': t.start_time.isoformat(),
            'end_time': t.end_time.isoformat() if t.end_time else None,
            'passenger_count': t.passenger_count,
            'duration_minutes': round(
                (t.end_time - t.start_time).total_seconds() / 60
            ) if t.end_time else None,
        } for t in trips]
    })

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def passenger_trip_history(request):
    user = request.user
    sessions = LocationSharingSession.objects.filter(user=user).order_by('-created_at')[:20]
    return success({
        'trips': [{
            'id': s.id,
            'from_stop': s.from_stop.name if s.from_stop else None,
            'to_stop': s.to_stop.name if s.to_stop else None,
            'date': s.created_at.strftime('%Y-%m-%d'),
            'time': s.created_at.strftime('%H:%M'),
            'is_active': s.is_active,
        } for s in sessions]
    })


# ========== SAFE LOCATION UPDATE (with fraud detection) ==========
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def update_location_safe(request):
    user = request.user
    if not user.is_driver:
        return error('Not a driver')
    if not user.is_approved:
        return error('Driver not approved')

    try:
        bus = user.bus
    except Exception:
        return error('No bus assigned')

    if not bus.is_active:
        return error('Start trip first')

    lat = request.data.get('lat')
    lng = request.data.get('lng')
    speed = float(request.data.get('speed', 0))

    if lat is None or lng is None:
        return error('lat and lng required — GPS unavailable?')

    try:
        lat = float(lat)
        lng = float(lng)
    except (ValueError, TypeError):
        return error('Invalid coordinates')

    if not validate_coordinates(lat, lng):
        return error('Coordinates out of Kashmir bounds')

    # GPS jump detection
    try:
        last_loc = bus.location
        if last_loc and last_loc.last_updated:
            elapsed = (timezone.now() - last_loc.last_updated).total_seconds()
            if detect_gps_jump(last_loc.lat, last_loc.lng, lat, lng, elapsed):
                log_suspicious(user, 'GPS_JUMP',
                    f'From {last_loc.lat},{last_loc.lng} to {lat},{lng} in {elapsed}s')
                return error('Invalid GPS data detected. Please retry.')
    except BusLocation.DoesNotExist:
        pass

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


# ========== SAFE WAITING (spam check) ==========
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def mark_waiting_safe(request):
    user = request.user

    if check_waiting_spam(user.id):
        log_suspicious(user, 'WAITING_SPAM', 'Too many waiting requests')
        return error('Too many requests. Wait a while.', 429)

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

    if not validate_coordinates(float(lat), float(lng)):
        return error('Invalid location')

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


# ========== DRIVER STATUS (public) ==========
@api_view(['GET'])
@permission_classes([AllowAny])
def get_buses_with_status(request):
    lat = request.query_params.get('lat')
    lng = request.query_params.get('lng')
    radius_m = float(request.query_params.get('radius_m', 5000))

    now = timezone.now()
    active_buses = Bus.objects.filter(
        is_active=True,
        location__isnull=False
    ).select_related('location', 'route', 'driver')

    result = []
    for bus in active_buses:
        loc = bus.location
        diff = (now - loc.last_updated).total_seconds()

        if diff > 600:
            bus.is_active = False
            bus.save()
            continue

        if diff <= 30:
            driver_status = 'active'
        elif diff <= 60:
            driver_status = 'active'
        else:
            driver_status = 'offline'
            continue

        active_trip = Trip.objects.filter(driver=bus.driver, status='paused').first() if bus.driver else None
        if active_trip:
            driver_status = 'paused'

        bus_data = {
            'bus_id': bus.id,
            'plate': bus.plate_number,
            'route': bus.route.name,
            'route_id': bus.route.id,
            'start': bus.route.start_point,
            'end': bus.route.end_point,
            'lat': loc.lat,
            'lng': loc.lng,
            'speed': loc.speed if hasattr(loc, 'speed') else 0,
            'driver_name': bus.driver.name if bus.driver else 'Unknown',
            'driver_status': driver_status,
            'is_verified': bus.driver.is_approved if bus.driver else False,
            'is_paused': driver_status == 'paused',
            'last_updated': loc.last_updated.isoformat(),
        }

        if lat and lng:
            try:
                dist = calculate_distance(float(lat), float(lng), loc.lat, loc.lng)
                if dist > radius_m:
                    continue
                bus_data['distance_m'] = round(dist)
                bus_data['eta_minutes'] = estimate_eta(loc.lat, loc.lng, float(lat), float(lng))
            except (ValueError, TypeError):
                pass

        result.append(bus_data)

    return success({'buses': result, 'count': len(result)})


# ========== ADMIN APIs ==========
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def admin_drivers(request):
    if not request.user.is_staff:
        return error('Admin only', 403)

    drivers = UserModel.objects.filter(is_driver=True).order_by('-created_at')
    return success({
        'drivers': [{
            'id': d.id,
            'name': d.name,
            'phone': d.phone,
            'bus_number': d.bus_number,
            'license_no': d.license_no,
            'rc_number': d.rc_number,
            'is_approved': d.is_approved,
            'docs_verified': d.docs_verified,
            'selfie_url': d.selfie_url,
            'created_at': d.created_at.isoformat(),
        } for d in drivers]
    })

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def admin_verify_driver(request):
    if not request.user.is_staff:
        return error('Admin only', 403)

    driver_id = request.data.get('driver_id')
    action = request.data.get('action', 'verify')

    try:
        driver = UserModel.objects.get(id=driver_id, is_driver=True)
    except UserModel.DoesNotExist:
        return error('Driver not found', 404)

    if action == 'verify':
        driver.is_approved = True
        driver.docs_verified = True
        driver.save()
        return success({'message': f'{driver.name} verified ✓'})
    elif action == 'reject':
        driver.is_approved = False
        driver.docs_verified = False
        driver.save()
        return success({'message': f'{driver.name} rejected'})

    return error('action must be verify or reject')

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def admin_active_trips(request):
    if not request.user.is_staff:
        return error('Admin only', 403)

    trips = Trip.objects.filter(
        status__in=['active', 'paused']
    ).select_related('driver', 'bus', 'route').order_by('-start_time')

    return success({
        'active_trips': [{
            'trip_id': t.id,
            'driver': t.driver.name,
            'phone': t.driver.phone,
            'bus': t.bus.plate_number,
            'route': t.route.name,
            'status': t.status,
            'start_time': t.start_time.isoformat(),
            'passenger_count': t.passenger_count,
        } for t in trips]
    })

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def admin_emergency_alerts(request):
    if not request.user.is_staff:
        return error('Admin only', 403)

    alerts = EmergencyAlert.objects.filter(resolved=False).select_related('driver', 'trip').order_by('-timestamp')
    return success({
        'alerts': [{
            'alert_id': a.id,
            'driver': a.driver.name,
            'phone': a.driver.phone,
            'lat': a.latitude,
            'lng': a.longitude,
            'timestamp': a.timestamp.isoformat(),
            'resolved': a.resolved,
        } for a in alerts]
    })


# ========== HEALTH CHECK ==========
# ========== HEALTH CHECK ==========
@api_view(['GET'])
@permission_classes([AllowAny])
def health_check(request):
    import time
    from django.conf import settings
    start = time.time()
    checks = {}

    try:
        connection.ensure_connection()
        checks['database'] = 'ok'
    except Exception as e:
        checks['database'] = f'error: {str(e)}'

    try:
        cache.set('health_ping', 'pong', timeout=5)
        val = cache.get('health_ping')
        checks['redis'] = 'ok' if val == 'pong' else 'error'
    except Exception as e:
        checks['redis'] = f'error: {str(e)}'

    checks['response_ms'] = round((time.time() - start) * 1000)
    all_ok = all(v == 'ok' for k, v in checks.items() if k != 'response_ms')

    return Response({
        'status': 'healthy' if all_ok else 'degraded',
        'app': getattr(settings, 'APP_NAME', 'ITP'),
        'version': getattr(settings, 'APP_VERSION', '1.0.0'),
        'checks': checks,
    }, status=200 if all_ok else 503)

# ========== BUS DETAIL ==========
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
        'vehicle_type': bus.vehicle_type,
        'icon': '🚌' if bus.vehicle_type == 'bus' else '🚐',
        'route': bus.route.name,
        'route_id': bus.route.id,
        'start': bus.route.start_point,
        'end': bus.route.end_point,
        'driver_name': bus.driver.name if bus.driver else 'Unknown',
        'is_verified': bus.driver.is_approved if bus.driver else False,
        'is_verified_blue_tick': bus.driver.docs_verified if bus.driver else False,
        'stops': [{
            'id': rs.stop.id,
            'name': rs.stop.name,
            'lat': rs.stop.lat,
            'lng': rs.stop.lng,
            'order': rs.order,
        } for rs in stops],
        'lat': loc.lat,
        'lng': loc.lng,
        'speed': loc.speed,
        'last_updated': loc.last_updated.isoformat(),
    })

from .models import (RideRequest, RideDriverOffer, 
                     NoShowLog, UserViolation, 
                     VEHICLE_CONFIG, VEHICLE_PRICING)

# ─── VEHICLE CONFIG API ────────────────────────────────────

@api_view(['GET'])
@permission_classes([AllowAny])
def get_vehicle_config(request):
    """Frontend ko batao kaunse vehicles enabled hain"""
    return success({
        'vehicles': [
            {
                'type': k,
                'name': v['name'],
                'icon': v['icon'],
                'enabled': v['enabled'],
                'mode': v['type'],
                'pricing': VEHICLE_PRICING.get(k, None),
            }
            for k, v in VEHICLE_CONFIG.items()
        ]
    })

# ─── FARE CALCULATION ──────────────────────────────────────

def calculate_fare(vehicle_type, distance_km):
    pricing = VEHICLE_PRICING.get(vehicle_type, {'base': 30, 'per_km': 10})
    fare = pricing['base'] + (pricing['per_km'] * distance_km)
    return round(fare, 2)

def calculate_distance_km(lat1, lng1, lat2, lng2):
    dist_m = calculate_distance(lat1, lng1, lat2, lng2)
    return round(dist_m / 1000, 2)

# ─── BOOK RIDE ─────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def book_ride(request):
    user = request.user

    # Check suspended account
    no_show_count = NoShowLog.objects.filter(passenger=user).count()
    if no_show_count >= 5:
        return error('Account suspended due to repeated no-shows. Contact support.', 403)

    vehicle_type = request.data.get('vehicle_type', '').strip()
    pickup_lat = request.data.get('pickup_lat')
    pickup_lng = request.data.get('pickup_lng')
    dest_lat = request.data.get('dest_lat')
    dest_lng = request.data.get('dest_lng')
    pickup_address = request.data.get('pickup_address', '')
    dest_address = request.data.get('dest_address', '')

    if not all([vehicle_type, pickup_lat, pickup_lng, dest_lat, dest_lng]):
        return error('vehicle_type, pickup_lat, pickup_lng, dest_lat, dest_lng required')

    # Check vehicle enabled
    config = VEHICLE_CONFIG.get(vehicle_type)
    if not config or not config['enabled']:
        return error(f'{vehicle_type} is not available yet. Coming soon!', 400, 'COMING_SOON')

    if config['type'] != 'booking':
        return error('This vehicle type does not support booking', 400)

    # Check active ride
    active = RideRequest.objects.filter(
        passenger=user,
        status__in=['searching', 'accepted', 'arrived', 'started']
    ).first()
    if active:
        return error('You already have an active ride', 400)

    # Calculate fare
    dist_km = calculate_distance_km(
        float(pickup_lat), float(pickup_lng),
        float(dest_lat), float(dest_lng)
    )
    fare = calculate_fare(vehicle_type, dist_km)

    # Create ride request
    ride = RideRequest.objects.create(
        passenger=user,
        vehicle_type=vehicle_type,
        pickup_lat=float(pickup_lat),
        pickup_lng=float(pickup_lng),
        pickup_address=pickup_address,
        dest_lat=float(dest_lat),
        dest_lng=float(dest_lng),
        dest_address=dest_address,
        distance_km=dist_km,
        estimated_fare=fare,
        status='searching',
    )

    # Find nearest drivers (2km radius)
    notify_nearby_drivers(ride)

    return success({
        'ride_id': ride.id,
        'status': 'searching',
        'estimated_fare': fare,
        'distance_km': dist_km,
        'vehicle_type': vehicle_type,
        'icon': config['icon'],
        'message': 'Looking for nearby drivers...',
    })

def notify_nearby_drivers(ride, radius_m=2000):
    """Find nearby approved drivers and send FCM"""
    from users.models import User as UserModel

    # Get drivers with same vehicle type who are active
    drivers = UserModel.objects.filter(
        is_driver=True,
        is_approved=True,
        vehicle_type=ride.vehicle_type,
        fcm_token__isnull=False,
    ).exclude(
        ride_assignments__status__in=['accepted', 'arrived', 'started']
    )

    notified = []
    for driver in drivers:
        # Check if driver has recent location
        try:
            bus = driver.bus
            loc = bus.location
            diff = (timezone.now() - loc.last_updated).total_seconds()
            if diff > 300:  # 5 min se zyada purana
                continue

            dist = calculate_distance(
                ride.pickup_lat, ride.pickup_lng,
                loc.lat, loc.lng
            )

            if dist <= radius_m:
                RideDriverOffer.objects.create(
                    ride=ride,
                    driver=driver,
                )
                notified.append(driver.fcm_token)

        except Exception:
            continue

    # Send FCM
    if notified:
        try:
            from .firebase import send_bulk_notification
            send_bulk_notification(
                tokens=notified,
                title='🛺 New Ride Request!',
                body=f'{ride.vehicle_type.title()} — {ride.pickup_address or "Nearby"} → {ride.dest_address or "Destination"}',
                data={
                    'ride_id': str(ride.id),
                    'type': 'ride_request',
                    'pickup_lat': str(ride.pickup_lat),
                    'pickup_lng': str(ride.pickup_lng),
                    'estimated_fare': str(ride.estimated_fare),
                    'distance_km': str(ride.distance_km),
                }
            )
        except Exception as e:
            print(f"FCM error: {e}")

    # If no drivers found after 60 sec — auto cancel
    # (Frontend poll kare /v1/ride/{id}/status/)

# ─── DRIVER ACCEPT/REJECT ──────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def respond_to_ride(request):
    user = request.user
    if not user.is_driver:
        return error('Not a driver')

    ride_id = request.data.get('ride_id')
    action = request.data.get('action')  # accept / reject

    if action not in ['accept', 'reject']:
        return error('action must be accept or reject')

    try:
        ride = RideRequest.objects.get(id=ride_id, status='searching')
    except RideRequest.DoesNotExist:
        return error('Ride not available or already taken', 404)

    try:
        offer = RideDriverOffer.objects.get(ride=ride, driver=user)
    except RideDriverOffer.DoesNotExist:
        return error('You were not offered this ride', 403)

    if action == 'reject':
        offer.response = 'rejected'
        offer.responded_at = timezone.now()
        offer.save()
        return success({'message': 'Ride rejected'})

    # Accept
    if RideRequest.objects.filter(
        driver=user,
        status__in=['accepted', 'arrived', 'started']
    ).exists():
        return error('You already have an active ride')

    ride.driver = user
    ride.status = 'accepted'
    ride.accepted_at = timezone.now()
    ride.save()

    offer.response = 'accepted'
    offer.responded_at = timezone.now()
    offer.save()

    # Notify passenger
    if ride.passenger.fcm_token:
        try:
            from .firebase import send_bulk_notification
            send_bulk_notification(
                tokens=[ride.passenger.fcm_token],
                title='🎉 Driver Found!',
                body=f'{user.name} is on the way!',
                data={
                    'ride_id': str(ride.id),
                    'type': 'driver_accepted',
                    'driver_name': user.name,
                    'driver_phone': user.phone,
                }
            )
        except Exception as e:
            print(f"FCM error: {e}")

    return success({
        'message': 'Ride accepted',
        'ride_id': ride.id,
        'passenger_name': ride.passenger.name,
        'pickup_lat': ride.pickup_lat,
        'pickup_lng': ride.pickup_lng,
        'pickup_address': ride.pickup_address,
        'dest_lat': ride.dest_lat,
        'dest_lng': ride.dest_lng,
        'estimated_fare': ride.estimated_fare,
    })

# ─── DRIVER ARRIVED ────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def driver_arrived(request):
    user = request.user
    ride_id = request.data.get('ride_id')

    try:
        ride = RideRequest.objects.get(id=ride_id, driver=user, status='accepted')
    except RideRequest.DoesNotExist:
        return error('Ride not found', 404)

    ride.status = 'arrived'
    ride.arrived_at = timezone.now()
    ride.save()

    # Notify passenger
    if ride.passenger.fcm_token:
        try:
            from .firebase import send_bulk_notification
            send_bulk_notification(
                tokens=[ride.passenger.fcm_token],
                title='🚗 Driver has arrived!',
                body='Your driver is waiting. Please hurry!',
                data={'ride_id': str(ride.id), 'type': 'driver_arrived'}
            )
        except Exception:
            pass

    return success({
        'message': 'Marked as arrived',
        'arrived_at': ride.arrived_at.isoformat(),
        'wait_free_minutes': 2,
    })

# ─── START RIDE ────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def start_ride(request):
    user = request.user
    ride_id = request.data.get('ride_id')

    try:
        ride = RideRequest.objects.get(
            id=ride_id, driver=user,
            status__in=['accepted', 'arrived']
        )
    except RideRequest.DoesNotExist:
        return error('Ride not found', 404)

    ride.status = 'started'
    ride.started_at = timezone.now()
    ride.save()

    return success({'message': 'Ride started', 'started_at': ride.started_at.isoformat()})

# ─── COMPLETE RIDE ─────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def complete_ride(request):
    user = request.user
    ride_id = request.data.get('ride_id')
    payment_method = request.data.get('payment_method', 'cash')

    try:
        ride = RideRequest.objects.get(id=ride_id, driver=user, status='started')
    except RideRequest.DoesNotExist:
        return error('Ride not found', 404)

    ride.status = 'completed'
    ride.completed_at = timezone.now()
    ride.final_fare = ride.estimated_fare
    ride.payment_status = 'cash' if payment_method == 'cash' else 'pending'
    ride.save()

    # Notify passenger
    if ride.passenger.fcm_token:
        try:
            from .firebase import send_bulk_notification
            send_bulk_notification(
                tokens=[ride.passenger.fcm_token],
                title='✅ Ride Completed!',
                body=f'Total fare: ₹{ride.final_fare}',
                data={'ride_id': str(ride.id), 'type': 'ride_completed'}
            )
        except Exception:
            pass

    return success({
        'message': 'Ride completed',
        'final_fare': ride.final_fare,
        'distance_km': ride.distance_km,
        'payment_status': ride.payment_status,
    })

# ─── CANCEL RIDE ───────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def cancel_ride(request):
    user = request.user
    ride_id = request.data.get('ride_id')
    reason = request.data.get('reason', '')

    try:
        ride = RideRequest.objects.get(
            id=ride_id,
            status__in=['searching', 'accepted', 'arrived']
        )
    except RideRequest.DoesNotExist:
        return error('Ride not found', 404)

    # Check who is cancelling
    if ride.passenger == user:
        cancelled_by = 'passenger'
    elif ride.driver == user:
        cancelled_by = 'driver'
    else:
        return error('Not authorized', 403)

    # Cancellation fee logic
    cancellation_fee = 0
    if cancelled_by == 'passenger' and ride.accepted_at:
        elapsed = (timezone.now() - ride.accepted_at).total_seconds()
        if elapsed > 120:  # 2 min ke baad
            cancellation_fee = 20  # ₹20 fee

    ride.status = 'cancelled'
    ride.cancelled_by = cancelled_by
    ride.cancel_reason = reason
    ride.cancellation_fee = cancellation_fee
    ride.save()

    # Log violation if passenger cancels repeatedly
    if cancelled_by == 'passenger' and cancellation_fee > 0:
        UserViolation.objects.create(
            user=user,
            violation_type='late_cancellation',
            detail=f'Ride {ride.id} cancelled after 2 min'
        )

    return success({
        'message': 'Ride cancelled',
        'cancellation_fee': cancellation_fee,
        'fee_note': '₹20 cancellation fee applied' if cancellation_fee > 0 else 'No cancellation fee',
    })

# ─── NO SHOW ───────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def mark_no_show(request):
    user = request.user
    if not user.is_driver:
        return error('Not a driver')

    ride_id = request.data.get('ride_id')

    try:
        ride = RideRequest.objects.get(id=ride_id, driver=user, status='arrived')
    except RideRequest.DoesNotExist:
        return error('Ride not found', 404)

    # Check 3 min wait
    if ride.arrived_at:
        elapsed = (timezone.now() - ride.arrived_at).total_seconds()
        if elapsed < 180:  # 3 min wait required
            remaining = int((180 - elapsed) / 60)
            return error(f'Please wait {remaining} more minutes before marking no-show')

    ride.status = 'no_show'
    ride.no_show_marked_at = timezone.now()
    ride.cancellation_fee = 30  # ₹30 no-show fee
    ride.save()

    # Log no show
    no_show = NoShowLog.objects.create(
        passenger=ride.passenger,
        ride=ride,
    )

    # Check suspension
    total_no_shows = NoShowLog.objects.filter(passenger=ride.passenger).count()
    suspended = total_no_shows >= 5

    if suspended:
        UserViolation.objects.create(
            user=ride.passenger,
            violation_type='account_suspended',
            detail=f'5 no-shows reached'
        )

    # Notify passenger
    if ride.passenger.fcm_token:
        try:
            from .firebase import send_bulk_notification
            msg = 'Account suspended due to repeated no-shows.' if suspended else f'No-show recorded. ₹30 fee applied. ({total_no_shows}/5)'
            send_bulk_notification(
                tokens=[ride.passenger.fcm_token],
                title='⚠️ No-Show Recorded',
                body=msg,
                data={'type': 'no_show', 'ride_id': str(ride.id)}
            )
        except Exception:
            pass

    return success({
        'message': 'No-show recorded',
        'no_show_fee': 30,
        'total_no_shows': total_no_shows,
        'account_suspended': suspended,
    })

# ─── RIDE STATUS POLL ──────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_ride_status(request, ride_id):
    try:
        ride = RideRequest.objects.get(id=ride_id)
    except RideRequest.DoesNotExist:
        return error('Ride not found', 404)

    if ride.passenger != request.user and ride.driver != request.user:
        return error('Not authorized', 403)

    data = {
        'ride_id': ride.id,
        'status': ride.status,
        'vehicle_type': ride.vehicle_type,
        'estimated_fare': ride.estimated_fare,
        'final_fare': ride.final_fare,
        'distance_km': ride.distance_km,
        'pickup_lat': ride.pickup_lat,
        'pickup_lng': ride.pickup_lng,
        'dest_lat': ride.dest_lat,
        'dest_lng': ride.dest_lng,
        'cancellation_fee': ride.cancellation_fee,
    }

    if ride.driver:
        data['driver'] = {
            'name': ride.driver.name,
            'phone': ride.driver.phone,
            'vehicle_type': ride.driver.vehicle_type,
            'bus_number': ride.driver.bus_number,
        }

    return success(data)

# ─── MY RIDES (PASSENGER) ──────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_rides(request):
    rides = RideRequest.objects.filter(
        passenger=request.user
    ).order_by('-created_at')[:20]

    return success({
        'rides': [{
            'ride_id': r.id,
            'vehicle_type': r.vehicle_type,
            'status': r.status,
            'pickup_address': r.pickup_address,
            'dest_address': r.dest_address,
            'estimated_fare': r.estimated_fare,
            'final_fare': r.final_fare,
            'date': r.created_at.strftime('%Y-%m-%d %H:%M'),
        } for r in rides]
    })

# ─── DRIVER RIDE HISTORY ───────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def driver_rides(request):
    if not request.user.is_driver:
        return error('Not a driver')

    rides = RideRequest.objects.filter(
        driver=request.user
    ).order_by('-created_at')[:20]

    return success({
        'rides': [{
            'ride_id': r.id,
            'vehicle_type': r.vehicle_type,
            'status': r.status,
            'pickup_address': r.pickup_address,
            'dest_address': r.dest_address,
            'final_fare': r.final_fare,
            'date': r.created_at.strftime('%Y-%m-%d %H:%M'),
        } for r in rides]
    })

# ─── ESTIMATE FARE (before booking) ───────────────────────

@api_view(['POST'])
@permission_classes([AllowAny])
def estimate_fare(request):
    vehicle_type = request.data.get('vehicle_type')
    pickup_lat = request.data.get('pickup_lat')
    pickup_lng = request.data.get('pickup_lng')
    dest_lat = request.data.get('dest_lat')
    dest_lng = request.data.get('dest_lng')

    if not all([vehicle_type, pickup_lat, pickup_lng, dest_lat, dest_lng]):
        return error('All fields required')

    config = VEHICLE_CONFIG.get(vehicle_type)
    if not config:
        return error('Invalid vehicle type')

    dist_km = calculate_distance_km(
        float(pickup_lat), float(pickup_lng),
        float(dest_lat), float(dest_lng)
    )
    fare = calculate_fare(vehicle_type, dist_km)
    pricing = VEHICLE_PRICING.get(vehicle_type, {})

    return success({
        'vehicle_type': vehicle_type,
        'icon': config['icon'],
        'distance_km': dist_km,
        'estimated_fare': fare,
        'base_fare': pricing.get('base', 0),
        'per_km': pricing.get('per_km', 0),
        'breakdown': f"₹{pricing.get('base', 0)} base + ₹{pricing.get('per_km', 0)}/km × {dist_km}km",
    })