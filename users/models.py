from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
import random
import hashlib

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
        now = timezone.now()
        # Reset window if 1 hour passed
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

        # 30 sec cooldown between requests
        if self.last_otp_sent:
            gap = (now - self.last_otp_sent).total_seconds()
            if gap < 30:
                return None, f'Wait {int(30 - gap)} seconds before requesting again'

        # Generate new OTP (valid for 5 minutes)
        self.otp = str(random.randint(100000, 999999))
        self.otp_expires = now + timezone.timedelta(minutes=5)
        self.otp_count += 1
        self.last_otp_sent = now
        self.save()
        return self.otp, None

    def __str__(self):
        return f"{self.name} ({'Driver' if self.is_driver else 'Passenger'})"

class RefreshTokenStore(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='refresh_tokens')
    token_hash = models.CharField(max_length=64, unique=True)
    device_fingerprint = models.CharField(max_length=200, blank=True, null=True)
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_revoked = models.BooleanField(default=False)

    @staticmethod
    def hash_token(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def __str__(self):
        return f"{self.user.name} — {'revoked' if self.is_revoked else 'active'}"

class OTPAttemptLog(models.Model):
    phone = models.CharField(max_length=15)
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    device_fingerprint = models.CharField(max_length=200, blank=True, null=True)
    otp_entered = models.CharField(max_length=6)
    success = models.BooleanField(default=False)
    attempted_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.phone} — {'✓' if self.success else '✗'} @ {self.attempted_at}"
  
selfie_hash = models.CharField(max_length=64, blank=True, null=True)