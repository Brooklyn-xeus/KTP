from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from . import views

urlpatterns = [
    path('auth/register/', views.register),
    path('auth/login/', views.login),
    path('auth/refresh/', TokenRefreshView.as_view()),
    path('auth/profile/', views.profile),
    path('auth/fcm/', views.update_fcm),
    path('auth/create-admin/', views.create_admin),]
