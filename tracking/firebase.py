import firebase_admin
from firebase_admin import credentials, messaging
import json
import os

def initialize_firebase():
    if not firebase_admin._apps:
        # Download service account JSON from Firebase Console
        # Project Settings → Service Accounts → Generate new private key
        # Save as firebase_credentials.json in KTP root folder
        cred_path = '/storage/emulated/0/WHY/KTP/firebase_credentials.json'
        if os.path.exists(cred_path):
            cred = credentials.Certificate(cred_path)
            firebase_admin.initialize_app(cred)
            return True
    return True

def send_notification(fcm_token, title, body, data=None):
    try:
        initialize_firebase()
        message = messaging.Message(
            notification=messaging.Notification(
                title=title,
                body=body,
            ),
            data=data or {},
            token=fcm_token,
        )
        messaging.send(message)
        return True
    except Exception as e:
        print(f"FCM Error: {e}")
        return False

def send_bulk_notification(tokens, title, body, data=None):
    try:
        initialize_firebase()
        message = messaging.MulticastMessage(
            notification=messaging.Notification(
                title=title,
                body=body,
            ),
            data=data or {},
            tokens=tokens,
        )
        messaging.send_each_for_multicast(message)
        return True
    except Exception as e:
        print(f"FCM Bulk Error: {e}")
        return False
