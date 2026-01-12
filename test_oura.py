from wearables.models import WearableConnection
from django.contrib.auth.models import User

# Check all Oura connections
oura_connections = WearableConnection.objects.filter(device_type='oura', is_active=True)
print("Oura Connections:")
for connection in oura_connections:
    print(f"User: {connection.user.username}")
    print(f"External User ID: {connection.external_user_id}")
    print(f"Access Token Expiry: {connection.expires_at}")
    print("---")