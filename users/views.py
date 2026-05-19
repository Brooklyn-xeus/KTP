from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from django.utils import timezone
from django.core.cache import cache
from django.db import transaction
from .models import User, RefreshTokenStore, OTPAttemptLog
import uuid
import logging
import sentry_sdk
from PIL import Image
from decouple import config
import io
import hashlib
import requests as req

logger = logging.getLogger(__name__)

# ─── ERROR CODES ───────────────────────────────────────────

def success(data):
    return Response({'success': True, 'data': data})

def error(msg, status=400, code='VALIDATION_ERROR'):
    return Response({
        'success': False,
        'error_code': code,
        'message': msg
    }, status=status)

def auth_error(msg, status=401):
    return error(msg, status, 'AUTH_ERROR')

def rate_limit_error(msg):
    return error(msg, 429, 'RATE_LIMIT')

def server_error(msg='Something went wrong'):
    return error(msg, 500, 'SERVER_ERROR')

# ─── HELPERS ───────────────────────────────────────────────

def get_client_ip(request):
    x_forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded:
        return x_forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')

def get_tokens(user, request=None):
    refresh = RefreshToken.for_user(user)
    refresh['name'] = user.name
    refresh['is_driver'] = user.is_driver
    refresh['is_approved'] = user.is_approved

    # Store refresh token hash
    from django.utils import timezone
    expires_at = timezone.now() + timezone.timedelta(days=7)
    token_hash = RefreshTokenStore.hash_token(str(refresh))

    RefreshTokenStore.objects.create(
        user=user,
        token_hash=token_hash,
        ip_address=get_client_ip(request) if request else None,
        expires_at=expires_at,
    )

    return {
        'access': str(refresh.access_token),
        'refresh': str(refresh),
        'access_expires_in': 1800,  # 30 min in seconds
    }

def check_rate_limit(key, max_attempts=5, window=600):
    attempts = cache.get(key, 0)
    if attempts >= max_attempts:
        return False
    cache.set(key, attempts + 1, timeout=window)
    return True

def log_otp_attempt(phone, ip, device, otp_entered, success_flag):
    try:
        OTPAttemptLog.objects.create(
            phone=phone,
            ip_address=ip,
            device_fingerprint=device,
            otp_entered=otp_entered,
            success=success_flag,
        )
    except Exception as e:
        logger.error("OTP log error: %s", e)

def send_otp_sms(phone, otp):
    from decouple import config
    logger.info("OTP requested for %s", phone)
    api_key = config('FAST2SMS_API_KEY', default=None)
    if api_key:
        try:
            import requests as req
            response = req.post(
                "https://www.fast2sms.com/dev/bulkV2",
                headers={"authorization": api_key},
                json={
                    "route": "q",
                    "message": f"Your KTP OTP is {otp}. Valid 5 min. Do not share.",
                    "language": "english",
                    "flash": 0,
                    "numbers": phone,
                }
            )
            logger.info("SMS response: %s", response.json())
        except Exception as e:
            logger.error("SMS error: %s", e)
            sentry_sdk.capture_exception(e)
    else:
        print(f"📱 OTP for {phone}: {otp}")

# ─── PASSENGER GOOGLE AUTH ─────────────────────────────────

@api_view(['POST'])
@permission_classes([AllowAny])
def google_login(request):
    request_id = str(uuid.uuid4())[:8]
    id_token = request.data.get('id_token')
    device_fingerprint = request.data.get('device_fingerprint', '')
    ip = get_client_ip(request)

    if not id_token:
        return error('id_token required')

    # Rate limit by IP
    if not check_rate_limit(f'google_{ip}', max_attempts=10, window=3600):
        logger.warning("[%s] Google login rate limit: %s", request_id, ip)
        return rate_limit_error('Too many attempts. Try later.')

    try:
        import requests as req
        google_resp = req.get(
            f'https://oauth2.googleapis.com/tokeninfo?id_token={id_token}',
            timeout=5
        )
        google_data = google_resp.json()

        if 'error' in google_data:
            return auth_error('Invalid Google token')

        google_id = google_data.get('sub')
        email = google_data.get('email')
        name = google_data.get('name', email.split('@')[0] if email else 'User')

        if not google_id:
            return auth_error('Invalid Google token')

    except Exception as e:
        logger.error("[%s] Google verify error: %s", request_id, e)
        sentry_sdk.capture_exception(e)
        return server_error('Google verification failed')

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

    logger.info("[%s] Google login: %s (%s)", request_id, user.id, 'new' if created else 'existing')

    return success({
        'is_new': created,
        'user': {
            'name': user.name,
            'email': user.email,
            'is_driver': False,
        },
        'tokens': get_tokens(user, request)
    })

# ─── DRIVER REGISTER ───────────────────────────────────────

@api_view(['POST'])
@permission_classes([AllowAny])
def driver_register(request):
    vehicle_type = request.data.get('vehicle_type', 'bus').strip()
    if vehicle_type not in ['bus', 'sonu']:
        return error('vehicle_type must be bus or sonu')
        user.vehicle_type = vehicle_type
        user.save()
        request_id = str(uuid.uuid4())[:8]
        phone = request.data.get('phone', '').strip()
        name = request.data.get('name', '').strip()
        pin = request.data.get('pin', '').strip()
        license_no = request.data.get('license_no', '').strip()
        rc_number = request.data.get('rc_number', '').strip()
        bus_number = request.data.get('bus_number', '').strip()
        ip = get_client_ip(request)
        

    # Validate
    if not all([phone, name, pin, license_no, bus_number]):
        return error('phone, name, pin, license_no, bus_number required')

    if len(pin) != 4 or not pin.isdigit():
        return error('PIN must be 4 digits')

    if len(phone) != 10 or not phone.isdigit():
        return error('Invalid phone number')

    # Rate limit by phone + IP
    if not check_rate_limit(f'dreg_{phone}', 3, 3600):
        return rate_limit_error('Too many registration attempts')
    if not check_rate_limit(f'dreg_ip_{ip}', 5, 3600):
        return rate_limit_error('Too many requests from this device')

    # Duplicate check
    if User.objects.filter(phone=phone).exists():
        return error('Phone already registered')

    if User.objects.filter(bus_number=bus_number).exists():
        return error('Bus number already registered')

    with transaction.atomic():
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
        return rate_limit_error(err)

    send_otp_sms(phone, otp)
    logger.info("[%s] Driver registered: %s", request_id, phone)

    return success({
        'message': 'OTP sent. Verify your phone to complete registration.',
        'phone': phone,
    })

# ─── DRIVER VERIFY OTP ─────────────────────────────────────

@api_view(['POST'])
@permission_classes([AllowAny])
def driver_verify_otp(request):
    request_id = str(uuid.uuid4())[:8]
    phone = request.data.get('phone', '').strip()
    firebase_token = request.data.get('firebase_token', '').strip()
    otp = request.data.get('otp', '').strip()
    ip = get_client_ip(request)
    device = request.data.get('device_fingerprint', '')

    if not phone:
        return error('Phone required')

    # Rate limit OTP attempts
    if not check_rate_limit(f'otp_{phone}', 5, 600):
        log_otp_attempt(phone, ip, device, otp, False)
        return rate_limit_error('Too many OTP attempts. Wait 10 minutes.')

    try:
        user = User.objects.get(phone=phone, is_driver=True)
    except User.DoesNotExist:
        return error('Driver not found', 404)

    verified = False

    if firebase_token:
        try:
            import firebase_admin
            from firebase_admin import auth as firebase_auth
            if not firebase_admin._apps:
                from firebase_admin import credentials
                cred = credentials.Certificate('firebase_credentials.json')
                firebase_admin.initialize_app(cred)

            decoded = firebase_auth.verify_id_token(firebase_token)
            firebase_phone = decoded.get('phone_number', '').replace('+91', '').strip()

            if firebase_phone == phone:
                verified = True
            else:
                log_otp_attempt(phone, ip, device, 'firebase', False)
                return auth_error('Phone number mismatch')

        except Exception as e:
            logger.error("[%s] Firebase error: %s", request_id, e)
            return auth_error('Firebase verification failed')

    elif otp:
        if user.otp != otp:
            log_otp_attempt(phone, ip, device, otp, False)
            return auth_error('Wrong OTP')
        if timezone.now() > user.otp_expires:
            log_otp_attempt(phone, ip, device, otp, False)
            return auth_error('OTP expired')
        verified = True
    else:
        return error('firebase_token or otp required')

    if verified:
        log_otp_attempt(phone, ip, device, otp or 'firebase', True)
        # OTP invalidate
        user.otp = None
        user.otp_expires = None
        # NOT auto-approved — pending admin review
        user.save()

        logger.info("[%s] Driver OTP verified: %s — pending admin approval", request_id, phone)

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
                        'vehicle_type': user.vehicle_type or 'bus',
                    }
                )
                if not created and bus.driver is None:
                    bus.driver = user
                    bus.vehicle_type = user.vehicle_type or 'bus'
                    bus.save()
        except Exception as e:
            print(f"Bus error: {e}")

        return success({
            'message': 'Phone verified! Your documents are under review (24-48 hours). Contact support if needed.',
            'status': 'pending',
            'is_approved': False,
            'support_phone': '9999999999',
            'support_email': 'support@ktp.app',
        })

# ─── DRIVER LOGIN ──────────────────────────────────────────

@api_view(['POST'])
@permission_classes([AllowAny])
def driver_login(request):
    request_id = str(uuid.uuid4())[:8]
    phone = request.data.get('phone', '').strip()
    pin = request.data.get('pin', '').strip()
    ip = get_client_ip(request)

    if not phone or not pin:
        return error('Phone and PIN required')

    if not check_rate_limit(f'dlogin_{phone}', 5, 600):
        return rate_limit_error('Too many attempts. Wait 10 minutes.')

    if not check_rate_limit(f'dlogin_ip_{ip}', 10, 600):
        return rate_limit_error('Too many requests from this device.')

    try:
        user = User.objects.get(phone=phone, is_driver=True)
    except User.DoesNotExist:
        return auth_error('Driver not found')

    if user.pin != pin:
        logger.warning("[%s] Wrong PIN: %s", request_id, phone)
        return auth_error('Wrong PIN')

    if not user.is_active:
        return auth_error('Account disabled. Contact support.')

    if not user.is_approved:
        return Response({
            'success': False,
            'error_code': 'PENDING_APPROVAL',
            'message': 'Your account is under review (24-48 hours).',
            'status': 'pending',
            'support_phone': '9999999999',
            'support_email': 'support@ktp.app',
        }, status=403)

    fcm_token = request.data.get('fcm_token')
    if fcm_token:
        user.fcm_token = fcm_token
        user.save()

    logger.info("[%s] Driver login: %s", request_id, phone)

    return success({
        'user': {
            'phone': user.phone,
            'name': user.name,
            'is_driver': True,
            'is_approved': True,
            'selfie_url': user.selfie_url,
        },
        'tokens': get_tokens(user, request)
    })

# ─── LOGOUT ────────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def logout(request):
    refresh_token = request.data.get('refresh_token')
    if refresh_token:
        token_hash = RefreshTokenStore.hash_token(refresh_token)
        RefreshTokenStore.objects.filter(
            token_hash=token_hash
        ).update(is_revoked=True)

    logger.info("User %s logged out", request.user.id)
    return success({'message': 'Logged out successfully'})

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def logout_all_devices(request):
    RefreshTokenStore.objects.filter(
        user=request.user, is_revoked=False
    ).update(is_revoked=True)

    logger.info("User %s logged out all devices", request.user.id)
    return success({'message': 'Logged out from all devices'})

# ─── RESEND OTP ────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([AllowAny])
def resend_otp(request):
    phone = request.data.get('phone', '').strip()
    ip = get_client_ip(request)

    try:
        user = User.objects.get(phone=phone, is_driver=True)
    except User.DoesNotExist:
        return error('Driver not found', 404)

    otp, err = user.generate_otp()
    if err:
        return rate_limit_error(err)

    send_otp_sms(phone, otp)
    return success({'message': 'OTP resent'})

# ─── FORGOT + RESET PIN ────────────────────────────────────

@api_view(['POST'])
@permission_classes([AllowAny])
def forgot_pin(request):
    phone = request.data.get('phone', '').strip()
    ip = get_client_ip(request)

    if not check_rate_limit(f'forgot_{phone}_{ip}', 3, 3600):
        return rate_limit_error('Too many requests')

    try:
        user = User.objects.get(phone=phone, is_driver=True)
    except User.DoesNotExist:
        return error('Driver not found', 404)

    otp, err = user.generate_otp()
    if err:
        return rate_limit_error(err)

    send_otp_sms(phone, otp)
    return success({'message': 'OTP sent'})

@api_view(['POST'])
@permission_classes([AllowAny])
def reset_pin(request):
    phone = request.data.get('phone', '').strip()
    otp = request.data.get('otp', '').strip()
    new_pin = request.data.get('new_pin', '').strip()
    ip = get_client_ip(request)

    if not check_rate_limit(f'reset_{phone}', 5, 600):
        return rate_limit_error('Too many attempts')

    try:
        user = User.objects.get(phone=phone, is_driver=True)
    except User.DoesNotExist:
        return error('Driver not found', 404)

    if user.otp != otp:
        log_otp_attempt(phone, ip, '', otp, False)
        return auth_error('Wrong OTP')

    if timezone.now() > user.otp_expires:
        return auth_error('OTP expired')

    if len(new_pin) != 4 or not new_pin.isdigit():
        return error('PIN must be 4 digits')

    user.pin = new_pin
    user.otp = None
    user.otp_expires = None
    user.save()

    log_otp_attempt(phone, ip, '', otp, True)
    logger.info("PIN reset: %s", phone)

    return success({'message': 'PIN reset successfully'})

# ─── PROFILE + FCM ─────────────────────────────────────────

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

# ─── DRIVER SELFIE ─────────────────────────────────────────
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
        img = Image.open(selfie)
        img = img.convert('RGB')
        img.thumbnail((400, 400))
        buffer = io.BytesIO()
        img.save(buffer, format='WebP', quality=80)
        buffer.seek(0)

        # Duplicate check
        img_hash = hashlib.sha256(buffer.getvalue()).hexdigest()
        if User.objects.filter(selfie_hash=img_hash).exclude(id=user.id).exists():
            return error('Duplicate selfie detected')

        supabase_url = config('SUPABASE_URL')
        supabase_key = config('SUPABASE_KEY')
        bucket = 'ktp-selfies'
        file_path = f'selfies/{user.id}_selfie.webp'

        req.delete(
            f'{supabase_url}/storage/v1/object/{bucket}/{file_path}',
            headers={'Authorization': f'Bearer {supabase_key}'},
            timeout=10
        )

        response = req.post(
            f'{supabase_url}/storage/v1/object/{bucket}/{file_path}',
            headers={
                'Authorization': f'Bearer {supabase_key}',
                'Content-Type': 'image/webp',
            },
            data=buffer.getvalue(),
            timeout=10
        )

        if response.status_code not in [200, 201]:
            return error('Upload failed')

        public_url = f'{supabase_url}/storage/v1/object/public/{bucket}/{file_path}'
        user.selfie_url = public_url
        user.selfie_hash = img_hash
        user.save()

        return success({'message': 'Selfie uploaded', 'selfie_url': public_url})

    except Exception as e:
        logger.error("Selfie upload error: %s", e)
        sentry_sdk.capture_exception(e)
        return server_error('Upload failed')