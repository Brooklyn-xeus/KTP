from .models import Subscription, NotificationLog
from .firebase import send_notification, send_bulk_notification
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from django.core.cache import cache
from django.utils import timezone
from .models import BusLocation, PassengerWaiting
from buses.models import Bus, Route

def success(data):
    return Response({'success': True, 'data': data})

def error(msg, status=400):
    return Response({'success': False, 'message': msg}, status=status)

def validate_coordinates(lat, lng):
    return 20 <= lat <= 40 and 60 <= lng <= 85

# ─── DRIVER APIs ───────────────────────────────────────────

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

    bus.is_active = True
    bus.save()

    return success({'message': 'Trip started', 'bus_id': bus.id})

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

    if lat is None or lng is None:
        return error('lat and lng required')

    try:
        lat = float(lat)
        lng = float(lng)
    except (ValueError, TypeError):
        return error('Invalid coordinates')

    if not validate_coordinates(lat, lng):
        return error('Coordinates out of bounds')

    # Save to DB
    BusLocation.objects.update_or_create(
        bus=bus,
        defaults={'lat': lat, 'lng': lng}
    )

    # Save to cache
    cache_data = {
        'bus_id': bus.id,
        'lat': lat,
        'lng': lng,
        'route': bus.route.name,
        'plate': bus.plate_number,
        'last_updated': timezone.now().isoformat(),
    }
    cache.set(f'bus_location_{bus.id}', cache_data, timeout=60)

    return success({'message': 'Location updated'})

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

    bus.is_active = False
    bus.save()

    cache.delete(f'bus_location_{bus.id}')

    return success({'message': 'Trip ended'})

# ─── PASSENGER APIs ────────────────────────────────────────

@api_view(['GET'])
@permission_classes([AllowAny])
def get_buses(request):
    now = timezone.now()
    
    # Optional location filter
    lat = request.query_params.get('lat')
    lng = request.query_params.get('lng')
    radius_m = float(request.query_params.get('radius_m', 5000))
    
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

        if diff > 60:
            continue

        # Distance filter agar lat/lng diya hai
        if lat and lng:
            try:
                user_lat = float(lat)
                user_lng = float(lng)
                bus_lat = loc.lat
                bus_lng = loc.lng
                
                # Simple distance calculation (meters)
                import math
                R = 6371000
                dlat = math.radians(bus_lat - user_lat)
                dlng = math.radians(bus_lng - user_lng)
                a = (math.sin(dlat/2)**2 + 
                     math.cos(math.radians(user_lat)) * 
                     math.cos(math.radians(bus_lat)) * 
                     math.sin(dlng/2)**2)
                distance = R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
                
                if distance > radius_m:
                    continue
                    
            except (ValueError, TypeError):
                pass

        result.append({
            'bus_id': bus.id,
            'plate': bus.plate_number,
            'route': bus.route.name,
            'start': bus.route.start_point,
            'end': bus.route.end_point,
            'lat': loc.lat,
            'lng': loc.lng,
            'last_updated': loc.last_updated.isoformat(),
        })

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

    return success({
        'bus_id': bus.id,
        'plate': bus.plate_number,
        'route': bus.route.name,
        'start': bus.route.start_point,
        'end': bus.route.end_point,
        'stops': bus.route.stops,
        'lat': loc.lat,
        'lng': loc.lng,
        'last_updated': loc.last_updated.isoformat(),
    })

# ─── WAITING APIs ──────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def mark_waiting(request):
    user = request.user
    route_id = request.data.get('route_id')
    lat = request.data.get('lat')
    lng = request.data.get('lng')

    if not all([route_id, lat, lng]):
        return error('route_id, lat, lng required')

    try:
        route = Route.objects.get(id=route_id)
    except Route.DoesNotExist:
        return error('Route not found', 404)

    # Remove old waiting entry
    PassengerWaiting.objects.filter(
        user=user, route=route, got_bus=False
    ).delete()

    waiting = PassengerWaiting.objects.create(
        user=user,
        route=route,
        lat=float(lat),
        lng=float(lng)
    )

    return success({'message': 'Marked as waiting', 'id': waiting.id})

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def got_bus(request):
    user = request.user

    PassengerWaiting.objects.filter(
        user=user, got_bus=False
    ).update(got_bus=True)

    return success({'message': 'Marked as got bus'})

@api_view(['GET'])
@permission_classes([AllowAny])
def get_waiting_passengers(request, route_id):
    waiting = PassengerWaiting.objects.filter(
        route_id=route_id,
        got_bus=False
    ).values('lat', 'lng', 'user__name')

    return success({'passengers': list(waiting)})

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def subscribe_route(request):
    user = request.user
    route_id = request.data.get('route_id')
    time_window = request.data.get('time_window', '').upper()

    if not route_id or time_window not in ['AM', 'PM']:
        return error('route_id and time_window (AM/PM) required')

    try:
        route = Route.objects.get(id=route_id)
    except Route.DoesNotExist:
        return error('Route not found', 404)

    sub, created = Subscription.objects.get_or_create(
        user=user,
        route=route,
        time_window=time_window,
        defaults={'is_active': True}
    )

    if not created:
        sub.is_active = True
        sub.save()

    return success({
        'message': f'Subscribed to {route.name} ({time_window})',
        'subscription_id': sub.id
    })

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_subscriptions(request):
    subs = Subscription.objects.filter(
        user=request.user, is_active=True
    ).select_related('route')

    data = [{
        'id': s.id,
        'route': s.route.name,
        'start': s.route.start_point,
        'end': s.route.end_point,
        'time_window': s.time_window,
    } for s in subs]

    return success({'subscriptions': data})

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

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def trigger_notifications(request):
    """
    Call this when driver updates location
    Checks if bus entered route + time window matches
    Sends FCM to subscribers
    """
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

    # Get active subscribers for this route + time window
    subs = Subscription.objects.filter(
        route=bus.route,
        time_window=current_window,
        is_active=True,
        user__fcm_token__isnull=False
    ).exclude(
        # Skip if already notified today
        user__in=NotificationLog.objects.filter(
            route=bus.route,
            bus=bus,
            time_window=current_window,
            sent_at__date=now.date()
        ).values('user')
    ).select_related('user')

    if not subs:
        return success({'message': 'No subscribers to notify'})

    tokens = []
    notified_users = []

    for sub in subs:
        if sub.user.fcm_token:
            tokens.append(sub.user.fcm_token)
            notified_users.append(sub)

    if tokens:
        send_bulk_notification(
            tokens=tokens,
            title=f'🚌 Bus Coming — {bus.route.name}',
            body=f'Your bus is on the way! Check the app for live location.',
            data={
                'bus_id': str(bus.id),
                'route_id': str(bus.route.id),
            }
        )

        # Log notifications
        for sub in notified_users:
            NotificationLog.objects.get_or_create(
                user=sub.user,
                route=bus.route,
                bus=bus,
                time_window=current_window,
            )

    return success({
        'message': f'Notified {len(tokens)} subscribers',
        'window': current_window
    })

# Create your views here.
