# Create your views here.
from django.shortcuts import render
from django.http import JsonResponse

def bus_location(request):
    return JsonResponse({
        "bus_id": 1,
        "route": "Ganderbal → Srinagar",
        "latitude": 34.2268,
        "longitude": 74.7742,
        "status": "moving"
    })

