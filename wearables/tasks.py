from celery import shared_task
from django.contrib.auth.models import User
from .models import WearableConnection

def sync_oura_for_user(user, days_back=3):
    from .views import sync_oura_for_user as view_sync_oura
    return view_sync_oura(user, days_back)

def sync_whoop_for_user(user, days_back=3):
    from .views import sync_whoop_for_user as view_sync_whoop
    return view_sync_whoop(user, days_back)

@shared_task
def sync_user_wearables(user_id):
    try:
        user = User.objects.get(id=user_id)
        sync_oura_for_user(user)
        sync_whoop_for_user(user)
    except Exception as e:
        print(f"Sync error: {e}")

@shared_task
def sync_all_wearables():
    connections = WearableConnection.objects.filter(is_active=True)
    for connection in connections:
        sync_user_wearables.delay(connection.user.id)