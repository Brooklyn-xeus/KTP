from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from django.utils import timezone
from django.core.cache import cache
from .models import User
import requests as http_requests

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
    from decouple import config
    api_key = config('FAST2SMS_API_KEY', default=None)
    if not api_key:
        print(f"OTP for {phone}: {otp}")
        return True
    try:
        response = http_requests.post(
            "https://www.fast2sms.com/dev/bulkV2",
            json={
                "route": "otp",
                "variables_values": otp,
                "flash": 0,
                "numbers": phone,
            },
            headers={"authorization": api_key}
        )
        return response.json().get('return', False)
    except Exception as e:
        print(f"SMS Error: {e}")
        return False

def check_rate_limit(key, max_attempts=5, window=600):
    attempts = cache.get(key, 0)
    if attempts >= max_attempts:
        return False
    cache.set(key, attempts + 1, timeout=window)
    return True

# ─── PASSENGER — GOOGLE AUTH ──────────────────────────────

@api_view(['POST'])
@permission_classes([AllowAny])
def google_login(request):
    id_token = request.data.get('id_token')
    device_fingerprint = request.data.get('device_fingerprint', '')

    if not id_token:
        return error('id_token required')

    # Rate limit by device
    if device_fingerprint:
        rate_key = f'google_login_{device_fingerprint}'
        if not check_rate_limit(rate_key, max_attempts=10, window=3600):
            return error('Too many attempts. Try later.', 429)

    # Verify Google token
    try:
        google_response = http_requests.get(
            f'https://oauth2.googleapis.com/tokeninfo?id_token={id_token}'
        )
        google_data = google_response.json()

        if 'error' in google_data:
            return error('Invalid Google token')

        google_id = google_data.get('sub')
        email = google_data.get('email')
        name = google_data.get('name', email.split('@')[0] if email else 'User')

        if not google_id:
            return error('Invalid Google token')

    except Exception as e:
        return error(f'Token verification failed: {str(e)}')

    # Get or create user
    user, created = User.objects.get_or_create(
        google_id=google_id,
        defaults={
            'name': name,
            'email': email,
            'is_driver': False,
            'is_approved': True,
            'device_fingerprint': device_fingerprint,
        }
    )

    if not created and device_fingerprint:
        user.device_fingerprint = device_fingerprint
        user.save()

    return success({
        'message': 'Login successful',
        'is_new': created,
        'user': {
            'name': user.name,
            'email': user.email,
            'is_driver': False,
        },
        'tokens': get_tokens(user)
    })

# ─── DRIVER — PHONE + OTP ─────────────────────────────────

@api_view(['POST'])
@permission_classes([AllowAny])
def driver_register(request):
    phone = request.data.get('phone', '').strip()
    name = request.data.get('name', '').strip()
    pin = request.data.get('pin', '').strip()
    license_no = request.data.get('license_no', '').strip()
    rc_number = request.data.get('rc_number', '').strip()
    bus_number = request.data.get('bus_number', '').strip()

    if not all([phone, name, pin, license_no, bus_number]):
        return error('phone, name, pin, license_no, bus_number required')

    if len(pin) != 4 or not pin.isdigit():
        return error('PIN must be 4 digits')

    # Rate limit
    rate_key = f'driver_register_{phone}'
    if not check_rate_limit(rate_key, max_attempts=3, window=3600):
        return error('Too many attempts. Try after 1 hour.', 429)

    if User.objects.filter(phone=phone).exists():
        return error('Phone already registered')

    user = User.objects.create_user(
        phone=phone,
        name=name,
        is_driver=True,
        is_approved=False,
    )
    user.pin = pin
    user.license_no = license_no
    user.rc_number = rc_number
    user.bus_number = bus_number
    user.save()

    otp, err = user.generate_otp()
    if err:
        return error(err)

    send_otp_sms(phone, otp)

    return success({
        'message': 'OTP sent. Verify to complete registration.',
        'phone': phone,
    })

@api_view(['POST'])
@permission_classes([AllowAny])
def driver_upload_selfie(request):
    phone = request.data.get('phone', '').strip()
    selfie = request.FILES.get('selfie')

    if not phone or not selfie:
        return error('phone and selfie required')

    try:
        user = User.objects.get(phone=phone, is_driver=True)
    except User.DoesNotExist:
        return error('Driver not found', 404)

    if selfie.size > 5 * 1024 * 1024:
        return error('Max 5MB allowed')

    allowed = ['image/jpeg', 'image/png', 'image/webp']
    if selfie.content_type not in allowed:
        return error('Only JPEG, PNG, WebP allowed')

    try:
        from PIL import Image
        from decouple import config
        import io
        import requests as req

        # Compress to WebP
        img = Image.open(selfie)
        img = img.convert('RGB')
        img.thumbnail((400, 400))
        buffer = io.BytesIO()
        img.save(buffer, format='WebP', quality=80)
        buffer.seek(0)

        # Supabase Storage
        supabase_url = config('SUPABASE_URL')
        supabase_key = config('SUPABASE_KEY')
        bucket = 'ktp-selfies'
        file_path = f'selfies/{user.id}_selfie.webp'

        # Delete old if exists
        req.delete(
            f'{supabase_url}/storage/v1/object/{bucket}/{file_path}',
            headers={'Authorization': f'Bearer {supabase_key}'}
        )

        # Upload new
        response = req.post(
            f'{supabase_url}/storage/v1/object/{bucket}/{file_path}',
            headers={
                'Authorization': f'Bearer {supabase_key}',
                'Content-Type': 'image/webp',
            },
            data=buffer.getvalue()
        )

        if response.status_code not in [200, 201]:
            return error('Upload failed')

        public_url = f'{supabase_url}/storage/v1/object/public/{bucket}/{file_path}'
        user.selfie_url = public_url
        user.save()

        return success({
            'message': 'Selfie uploaded',
            'selfie_url': public_url
        })

    except Exception as e:
        print(f"Upload error: {e}")
        return error(f'Upload failed: {str(e)}')
@api_view(['POST'])
@permission_classes([AllowAny])
def driver_verify_otp(request):
    phone = request.data.get('phone', '').strip()
    otp = request.data.get('otp', '').strip()

    # Rate limit
    rate_key = f'otp_verify_{phone}'
    if not check_rate_limit(rate_key, max_attempts=5, window=600):
        return error('Too many attempts. Try later.', 429)

    try:
        user = User.objects.get(phone=phone, is_driver=True)
    except User.DoesNotExist:
        return error('Driver not found', 404)

    if user.otp != otp:
        return error('Wrong OTP')

    if timezone.now() > user.otp_expires:
        return error('OTP expired')

    # Verify karo — auto approve
    user.otp = None
    user.otp_expires = None
    user.is_approved = True
    user.save()

    # Auto create bus
    try:
        from buses.models import Bus, Route
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
        print(f"Bus error: {e}")

    return success({
        'message': 'Verified! You can start driving.',
        'is_approved': True,
        'user': {
            'phone': user.phone,
            'name': user.name,
            'is_driver': True,
            'is_approved': True,
            'selfie_url': user.selfie_url,
        },
        'tokens': get_tokens(user)
    })

@api_view(['POST'])
@permission_classes([AllowAny])
def driver_login(request):
    phone = request.data.get('phone', '').strip()
    pin = request.data.get('pin', '').strip()

    # Rate limit
    rate_key = f'driver_login_{phone}'
    if not check_rate_limit(rate_key, max_attempts=5, window=600):
        return error('Too many failed attempts. Wait 10 minutes.', 429)

    try:
        user = User.objects.get(phone=phone, is_driver=True)
    except User.DoesNotExist:
        return error('Driver not found', 404)

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
            'is_driver': True,
            'is_approved': user.is_approved,
            'selfie_url': user.selfie_url,
        },
        'tokens': get_tokens(user)
    })

@api_view(['POST'])
@permission_classes([AllowAny])
def resend_otp(request):
    phone = request.data.get('phone', '').strip()

    try:
        user = User.objects.get(phone=phone, is_driver=True)
    except User.DoesNotExist:
        return error('Driver not found', 404)

    otp, err = user.generate_otp()
    if err:
        return error(err)

    send_otp_sms(phone, otp)
    return success({'message': 'OTP resent'})

# ─── FORGOT PIN ───────────────────────────────────────────

@api_view(['POST'])
@permission_classes([AllowAny])
def forgot_pin(request):
    phone = request.data.get('phone', '').strip()

    try:
        user = User.objects.get(phone=phone, is_driver=True)
    except User.DoesNotExist:
        return error('Driver not found', 404)

    otp, err = user.generate_otp()
    if err:
        return error(err)

    send_otp_sms(phone, otp)
    return success({'message': 'OTP sent for PIN reset'})

@api_view(['POST'])
@permission_classes([AllowAny])
def reset_pin(request):
    phone = request.data.get('phone', '').strip()
    otp = request.data.get('otp', '').strip()
    new_pin = request.data.get('new_pin', '').strip()

    try:
        user = User.objects.get(phone=phone, is_driver=True)
    except User.DoesNotExist:
        return error('Driver not found', 404)

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

# ─── PROFILE ─────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def profile(request):
    user = request.user
    return success({
        'name': user.name,
        'email': user.email,
        'phone': user.phone,
        'is_driver': user.is_driver,
        'is_approved': user.is_approved,
        'selfie_url': user.selfie_url,
    })

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def update_fcm(request):
    token = request.data.get('fcm_token')
    if not token:
        return error('FCM token required')
    request.user.fcm_token = token
    request.user.save()
    return success({'message': 'FCM updated'})

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