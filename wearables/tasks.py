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
    except User.DoesNotExist:
        print(f"Sync error: user {user_id} not found")
        return

    active_device_types = set(
        WearableConnection.objects
        .filter(user=user, is_active=True)
        .values_list('device_type', flat=True)
    )

    if 'oura' in active_device_types:
        try:
            sync_oura_for_user(user)
        except Exception as e:
            print(f"Oura sync error for user {user_id}: {e}")

    if 'whoop' in active_device_types:
        try:
            sync_whoop_for_user(user)
        except Exception as e:
            print(f"Whoop sync error for user {user_id}: {e}")

@shared_task
def sync_all_wearables():
    # Dispatch one task per user (deduplicated), not one per connection
    user_ids = (
        WearableConnection.objects
        .filter(is_active=True)
        .values_list('user_id', flat=True)
        .distinct()
    )
    for user_id in user_ids:
        sync_user_wearables.delay(user_id)