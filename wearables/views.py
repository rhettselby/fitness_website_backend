from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from rest_framework_simplejwt.tokens import AccessToken
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from django.contrib.auth.models import User
import requests
import os
from datetime import datetime, timedelta
from .models import WearableConnection
from fitness.models import Cardio


OURA_CLIENT_ID = os.environ.get('OURA_CLIENT_ID')
OURA_CLIENT_SECRET = os.environ.get('OURA_CLIENT_SECRET')
OURA_REDIRECT_URI = os.environ.get('OURA_REDIRECT_URI')


#Helper Function
def get_user_from_token(request):
    
    auth_header = request.headers.get('Authorization', '')

    if not auth_header.startswith('Bearer '):
        return None
    
    token = auth_header.split(' ')[1]

    try:
        access_token = AccessToken(token)
        user_id = access_token['user_id']
        return User.objects.get(id=user_id)
    except (InvalidToken, TokenError, User.DoesNotExist):
        return None



@csrf_exempt
def oura_connect(request):
   
    user = get_user_from_token(request)

    if not user:
        return JsonResponse({'error': 'Authentication Required'}, status = 401)
    
    auth_url = (
        f"https://cloud.ouraring.com/oauth/authorize?"
        f"client_id={OURA_CLIENT_ID}&"
        f"redirect_url={OURA_REDIRECT_URI}&"
        f"response_type=code&"
        f"scope=workout&"
        f"state={user.id}"
    )

    return JsonResponse({"auth_url": auth_url})


@csrf_exempt
def oura_callback(request):

    code = request.Get.get('code')
    user_id = request.Get.get('state')

    if not code or not user_id:
        return JsonResponse({"error": "Invalid callback"}, status = 400)

    try:
        user = User.objects.get(id=user_id)

    except User.DoesNotExist:
        return JsonResponse({"error": "User Not Found"}, status = 404)
    
    # Exchange authorization code for access token
    token_response = requests.post(
        'https://api.ouraring.com/oauth/token',
        data={
            'grant_type': 'authorization_code',  
            'code': code,                         # The code Oura sent us
            'redirect_uri': OURA_REDIRECT_URI,   
            'client_id': OURA_CLIENT_ID,        
            'client_secret': OURA_CLIENT_SECRET,
        }
    )


    if token_response.status_code != 200:
        return JsonResponse({"error": "Failed to exchange token"}, status = 400)

    token_data = token_response.json()

# updates if finds user+oura, creates if does not find
    WearableConnection.objects.update_or_create(
        user = user,
        device_type = 'oura',
        defaults = {
            'access_token': token_data['access_token'],
            'refresh_token': token_data['refresh_token'],
            'expires_at': datetime.now() + timedelta(seconds = token_data['expires_in']),
            'is_active': True,
        }

    )

    return JsonResponse({"success": True, "message": "Oura connected!"})



@csrf_exempt
def sync_oura(request):

    user = get_user_from_token(request)

    if not user:
        return JsonResponse({"error": "Authentication Required"}, status = 401)
    
    try:
        connection = WearableConnection.objects.get(user = user, device_type = 'oura', is_active = True)

    except WearableConnection.DoesNotExist:
        return JsonResponse({"error": "Oura Not Connected"}, status = 400)
    
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=7)

    headers = {'Authorization': f'Bearer {connection.access_token}'}

    response = requests.get(
        f'https://api.ouraring.com/v2/usercollection/workout?start_date={start_date}&end_date={end_date}',
            headers=headers
        )
    
    if response.status_code != 200:
        return JsonResponse({"error": "Failed to fetch Oura data"}, status = 400)
    
    data = response.json()

    workouts_added = 0

    for workout in data.get('data', []):
        activity_type = workout.get('activity', 'Uknown Activity')
        _, created = Cardio.objects.get_or_create()
        user = user
        activity = f"Oura: {activity_type}",
        date = workout.get('stat_datetime')
        defaults = {
            'duration': workout.get('duration', 0) // 60
        }
        if created:
            workouts_added += 1

    connection.last_sync = datetime.now()
    connection.save()

    return JsonResponse({"success": True, "workouts_added": workouts_added})


