from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
import random

class UserManager(BaseUserManager):
    def create_user(self, phone=None, name=None, email=None, password=None, **extra_fields):
        user = self.model(name=name, **extra_fields)
        if phone:
            user.phone = phone
        if email:
            user.email = email.lower()
        user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, phone, name, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_approved', True)
        user = self.model(phone=phone, name=name, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

class User(AbstractBaseUser, PermissionsMixin):
    # Common
    name = models.CharField(max_length=100)
    is_driver = models.BooleanField(default=False)
    is_approved = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    fcm_token = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # Passenger — Google Auth
    email = models.EmailField(unique=True, blank=True, null=True)
    google_id = models.CharField(max_length=200, unique=True, blank=True, null=True)
    device_fingerprint = models.CharField(max_length=200, blank=True, null=True)

    # Driver — Phone Auth
    phone = models.CharField(max_length=15, unique=True, blank=True, null=True)
    pin = models.CharField(max_length=4, blank=True, null=True)
    otp = models.CharField(max_length=6, blank=True, null=True)
    otp_expires = models.DateTimeField(blank=True, null=True)
    otp_count = models.IntegerField(default=0)
    otp_window_start = models.DateTimeField(blank=True, null=True)
    last_otp_sent = models.DateTimeField(blank=True, null=True)

    # Driver documents
    license_no = models.CharField(max_length=50, blank=True, null=True)
    rc_number = models.CharField(max_length=50, blank=True, null=True)
    bus_number = models.CharField(max_length=20, blank=True, null=True)
    selfie_url = models.URLField(blank=True, null=True)
    docs_verified = models.BooleanField(default=False)

    USERNAME_FIELD = 'phone'
    REQUIRED_FIELDS = ['name']
    objects = UserManager()

    def generate_otp(self):
        from django.utils import timezone
        now = timezone.now()

        # Reset window agar 1 hour se zyada ho gaya
        if self.otp_window_start:
            diff = (now - self.otp_window_start).total_seconds()
            if diff > 3600:
                self.otp_count = 0
                self.otp_window_start = now
        else:
            self.otp_window_start = now

        # 3 OTP per hour limit
        if self.otp_count >= 3:
            return None, 'OTP limit reached. Try after 1 hour.'

        # 30 sec gap
        if self.last_otp_sent:
            gap = (now - self.last_otp_sent).total_seconds()
            if gap < 30:
                return None, f'Wait {int(30 - gap)} seconds'

        self.otp = str(random.randint(100000, 999999))
        self.otp_expires = now + timezone.timedelta(minutes=40)
        self.otp_count += 1
        self.last_otp_sent = now
        self.save()
        return self.otp, None

    def __str__(self):
        return f"{self.name} ({'Driver' if self.is_driver else 'Passenger'})"