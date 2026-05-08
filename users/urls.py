from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from . import views

urlpatterns = [
    # Passenger
    path('auth/google/', views.google_login),

    # Driver
    path('auth/driver/register/', views.driver_register),
    path('auth/driver/upload-selfie/', views.driver_upload_selfie),
    path('auth/driver/verify-otp/', views.driver_verify_otp),
    path('auth/driver/resend-otp/', views.resend_otp),
    path('auth/driver/login/', views.driver_login),

    # Forgot PIN
    path('auth/forgot-pin/', views.forgot_pin),
    path('auth/reset-pin/', views.reset_pin),

    # Session
    path('auth/logout/', views.logout),
    path('auth/logout-all/', views.logout_all_devices),

    # Common
    path('auth/refresh/', TokenRefreshView.as_view()),
    path('auth/profile/', views.profile),
    path('auth/fcm/', views.update_fcm),
    path('auth/create-admin/', views.create_admin),
]