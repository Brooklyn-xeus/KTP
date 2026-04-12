from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from .models import Route

def success(data):
    return Response({'success': True, 'data': data})

@api_view(['GET'])
@permission_classes([AllowAny])
def get_routes(request):
    routes = Route.objects.all().values(
        'id', 'name', 'start_point', 'end_point', 'stops'
    )
    return success({'routes': list(routes)})
