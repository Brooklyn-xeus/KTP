from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from . import views

urlpatterns = [
    # Passenger auth
    path('auth/register/', views.register),
    path('auth/login/', views.login_passenger),

    # Driver auth
    path('auth/driver/register/', views.driver_register),
    path('auth/driver/verify-otp/', views.driver_verify_otp),
    path('auth/driver/login/', views.driver_login),

    # Forgot PIN
    path('auth/forgot-pin/', views.forgot_pin),
    path('auth/reset-pin/', views.reset_pin),

    # Common
    path('auth/refresh/', TokenRefreshView.as_view()),
    path('auth/profile/', views.profile),
    path('auth/fcm/', views.update_fcm),
    path('auth/create-admin/', views.create_admin),
]
