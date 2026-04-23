from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from django.core.cache import cache
from django.utils import timezone
from django.db.models import Q
import math
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

@api_view(['GET'])
@permission_classes([AllowAny])
def get_bus_detail(request, bus_id):
    try:
        bus = Bus.objects.select_related('location', 'route', 'driver').get(id=bus_id, is_active=True)
    except Bus.DoesNotExist:
        return error('Bus not found', 404)

    try:
        loc = bus.location
    except BusLocation.DoesNotExist:
        return error('Location not available', 404)

    stops = RouteStop.objects.filter(route=bus.route).select_related('stop').order_by('order')

    return success({
        'bus_id': bus.id,
        'plate': bus.plate_number,
        'route': bus.route.name,
        'start': bus.route.start_point,
        'end': bus.route.end_point,
        'stops': [{'id': rs.stop.id, 'name': rs.stop.name, 'lat': rs.stop.lat, 'lng': rs.stop.lng} for rs in stops],
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

    try:
        bus = user.bus
        active_trip = Trip.objects.filter(driver=user, status='active').first()
        return success({
            'name': user.name,
            'phone': user.phone,
            'bus_number': bus.plate_number,
            'is_verified': user.is_approved,
            'trip_status': active_trip.status if active_trip else 'inactive',
            'trip_id': active_trip.id if active_trip else None,
        })
    except Bus.DoesNotExist:
        return error('No bus assigned')

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
            'routes': [{
                'id': route.id,
                'name': route.name,
                'start': route.start_point,
                'end': route.end_point,
                'is_frequent': route.id in frequent,
            }]
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
