from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
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

@api_view(['POST'])
@permission_classes([AllowAny])
def register(request):
    phone = request.data.get('phone', '').strip()
    name = request.data.get('name', '').strip()
    is_driver = request.data.get('is_driver', False)

    if not phone or not name:
        return error('Phone and name required')

    if User.objects.filter(phone=phone).exists():
        return error('User already exists')

    user = User.objects.create_user(
        phone=phone,
        name=name,
        is_driver=is_driver
    )

    return success({
        'message': 'Registered successfully',
        'user': {
            'phone': user.phone,
            'name': user.name,
            'is_driver': user.is_driver,
            'is_approved': user.is_approved,
        },
        'tokens': get_tokens(user)
    })

@api_view(['POST'])
@permission_classes([AllowAny])
def login(request):
    phone = request.data.get('phone', '').strip()

    if not phone:
        return error('Phone required')

    try:
        user = User.objects.get(phone=phone)
    except User.DoesNotExist:
        return error('User not found', 404)

    if not user.is_active:
        return error('Account disabled')

    # Update FCM token if provided
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

# Create your views here.
@api_view(['GET'])
@permission_classes([AllowAny])
def create_admin(request):
    if not User.objects.filter(phone='9999999999').exists():
        User.objects.create_superuser(
            phone='9999999999',
            name='Admin',
            password='Admin@1234'
        )
        return Response({'message': 'Admin created'})
    return Response({'message': 'Already exists'})
