from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from django.utils import timezone
from .models import User

def get_tokens(user):
    refresh = RefreshToken.for_user(user)
    refresh['name'] = user.name
    refresh['is_driver'] = user.is_driver
    refresh['is_approved'] = user.is_approved
    return {
        'access': str(refresh.access_token),
        'refresh': str(refresh),
    }

def success(data):
    return Response({'success': True, 'data': data})

def error(msg, status=400):
    return Response({'success': False, 'message': msg}, status=status)

def send_otp_sms(phone, otp):
    # Firebase SMS ya simple print for now
    print(f"OTP for {phone}: {otp}")
    return True

# ─── PASSENGER AUTH ────────────────────────────────────────

@api_view(['POST'])
@permission_classes([AllowAny])
def register(request):
    phone = request.data.get('phone', '').strip()
    name = request.data.get('name', '').strip()
    pin = request.data.get('pin', '').strip()
    is_driver = request.data.get('is_driver', False)

    if not phone or not name:
        return error('Phone and name required')

    if len(pin) != 4 or not pin.isdigit():
        return error('PIN must be 4 digits')

    if User.objects.filter(phone=phone).exists():
        return error('User already exists')

    user = User.objects.create_user(
        phone=phone,
        name=name,
        is_driver=is_driver
    )
    user.pin = pin
    user.save()

    return success({
        'message': 'Registered successfully',
        'user': {
            'phone': user.phone,
            'name': user.name,
            'is_driver': user.is_driver,
        },
        'tokens': get_tokens(user)
    })

@api_view(['POST'])
@permission_classes([AllowAny])
def login_passenger(request):
    phone = request.data.get('phone', '').strip()
    pin = request.data.get('pin', '').strip()

    if not phone or not pin:
        return error('Phone and PIN required')

    try:
        user = User.objects.get(phone=phone)
    except User.DoesNotExist:
        return error('User not found', 404)

    if user.is_driver:
        return error('Drivers use driver login')

    if user.pin != pin:
        return error('Wrong PIN')

    if not user.is_active:
        return error('Account disabled')

    fcm_token = request.data.get('fcm_token')
    if fcm_token:
        user.fcm_token = fcm_token
        user.save()

    return success({
        'user': {
            'phone': user.phone,
            'name': user.name,
            'is_driver': user.is_driver,
            'is_approved': user.is_approved,
        },
        'tokens': get_tokens(user)
    })

# ─── DRIVER AUTH ────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([AllowAny])
def driver_register(request):
    phone = request.data.get('phone', '').strip()
    name = request.data.get('name', '').strip()
    pin = request.data.get('pin', '').strip()
    license_no = request.data.get('license_no', '').strip()
    bus_number = request.data.get('bus_number', '').strip()

    if not phone or not name:
        return error('Phone and name required')

    if len(pin) != 4 or not pin.isdigit():
        return error('PIN must be 4 digits')

    if not license_no:
        return error('License number required')

    if not bus_number:
        return error('Bus number required')

    if User.objects.filter(phone=phone).exists():
        return error('User already exists')

    user = User.objects.create_user(
        phone=phone,
        name=name,
        is_driver=True,
        is_approved=False
    )
    user.pin = pin
    user.license_no = license_no
    user.bus_number = bus_number
    user.save()

    otp = user.generate_otp()
    send_otp_sms(phone, otp)

    return success({
        'message': 'Registration successful. OTP sent for verification.',
        'phone': phone,
    })

@api_view(['POST'])
@permission_classes([AllowAny])
def driver_verify_otp(request):
    phone = request.data.get('phone', '').strip()
    otp = request.data.get('otp', '').strip()

    try:
        user = User.objects.get(phone=phone, is_driver=True)
    except User.DoesNotExist:
        return error('Driver not found', 404)

    if user.otp != otp:
        return error('Wrong OTP')

    if timezone.now() > user.otp_expires:
        return error('OTP expired')

    # OTP verified — auto approve karo
    user.otp = None
    user.otp_expires = None
    user.is_approved = True  # ← AUTO APPROVE
    user.save()

    # Auto create bus
    from buses.models import Bus, Route
    try:
        route = Route.objects.first()
        if route and user.bus_number:
            bus, created = Bus.objects.get_or_create(
                plate_number=user.bus_number,
                defaults={
                    'route': route,
                    'driver': user,
                    'is_active': False,
                }
            )
            if not created and bus.driver is None:
                bus.driver = user
                bus.save()
    except Exception as e:
        print(f"Bus creation error: {e}")

    return success({
        'message': 'Phone verified. You can start driving!',
        'is_approved': True,
        'is_verified': False,  # Admin blue tick baad mein
        'user': {
            'phone': user.phone,
            'name': user.name,
            'is_driver': True,
            'is_approved': True,
        },
        'tokens': get_tokens(user)
    })

@api_view(['POST'])
@permission_classes([AllowAny])
def driver_login(request):
    phone = request.data.get('phone', '').strip()
    pin = request.data.get('pin', '').strip()

    if not phone or not pin:
        return error('Phone and PIN required')

    try:
        user = User.objects.get(phone=phone, is_driver=True)
    except User.DoesNotExist:
        return error('Driver not found', 404)

    if user.pin != pin:
        return error('Wrong PIN')

    if not user.is_approved:
        return error('Driver not approved yet')

    if not user.is_active:
        return error('Account disabled')

    fcm_token = request.data.get('fcm_token')
    if fcm_token:
        user.fcm_token = fcm_token
        user.save()

    return success({
        'user': {
            'phone': user.phone,
            'name': user.name,
            'is_driver': True,
            'is_approved': True,
        },
        'tokens': get_tokens(user)
    })

# ─── FORGOT PIN — OTP ───────────────────────────────────────

@api_view(['POST'])
@permission_classes([AllowAny])
def forgot_pin(request):
    phone = request.data.get('phone', '').strip()

    try:
        user = User.objects.get(phone=phone)
    except User.DoesNotExist:
        return error('User not found', 404)

    otp = user.generate_otp()
    send_otp_sms(phone, otp)

    return success({'message': 'OTP sent'})

@api_view(['POST'])
@permission_classes([AllowAny])
def reset_pin(request):
    phone = request.data.get('phone', '').strip()
    otp = request.data.get('otp', '').strip()
    new_pin = request.data.get('new_pin', '').strip()

    try:
        user = User.objects.get(phone=phone)
    except User.DoesNotExist:
        return error('User not found', 404)

    if user.otp != otp:
        return error('Wrong OTP')

    if timezone.now() > user.otp_expires:
        return error('OTP expired')

    if len(new_pin) != 4 or not new_pin.isdigit():
        return error('PIN must be 4 digits')

    user.pin = new_pin
    user.otp = None
    user.otp_expires = None
    user.save()

    return success({'message': 'PIN reset successfully'})

# ─── PROFILE ────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def profile(request):
    user = request.user
    return success({
        'phone': user.phone,
        'name': user.name,
        'is_driver': user.is_driver,
        'is_approved': user.is_approved,
    })

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def update_fcm(request):
    token = request.data.get('fcm_token')
    if not token:
        return error('FCM token required')
    request.user.fcm_token = token
    request.user.save()
    return success({'message': 'FCM token updated'})

@api_view(['GET'])
@permission_classes([AllowAny])
def create_admin(request):
    if not User.objects.filter(phone='9999999999').exists():
        User.objects.create_superuser(
            phone='9999999999',
            name='Admin',
            password='Admin@1234'
        )
        return success({'message': 'Admin created'})
    return success({'message': 'Already exists'})
