from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from rest_framework_simplejwt.tokens import AccessToken
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from django.contrib.auth.models import User
import os
from datetime import datetime, timedelta
from .models import WearableConnection
from fitness.models import Cardio
import requests
import hashlib
import hmac
import json


OURA_CLIENT_ID = os.environ.get('OURA_CLIENT_ID')
OURA_CLIENT_SECRET = os.environ.get('OURA_CLIENT_SECRET')
OURA_REDIRECT_URI = os.environ.get('OURA_REDIRECT_URI')
OURA_WEBHOOK_SECRET = os.environ.get('OURA_WEBHOOK_SECRET')


STRAVA_CLIENT_ID = os.environ.get('STRAVA_CLIENT_ID')
STRAVA_CLIENT_SECRET = os.environ.get('STRAVA_CLIENT_SECRET')
STRAVA_REDIRECT_URI = os.environ.get('STRAVA_REDIRECT_URI')




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
        f"redirect_uri={OURA_REDIRECT_URI}&"
        f"response_type=code&"
        f"scope=daily workout&"
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

    headers = {'Authorization': f'Bearer {token_data["access_token"]}'}
    user_info_response = requests.get( #python request for makking HTTP call to external API
        'https://api.ouraring.com/v2/usercollection/personal_info',
        headers=headers
    )

    oura_user_id = None
    if user_info_response.status_code == 200:
        oura_user_id = user_info_response.json().get('id')

# updates if finds user+oura, creates if does not find
    WearableConnection.objects.update_or_create(
        user = user,
        device_type = 'oura',
        defaults = {
            'access_token': token_data['access_token'],
            'refresh_token': token_data['refresh_token'],
            'expires_at': datetime.now() + timedelta(seconds = token_data['expires_in']),
            'is_active': True,
            'external_user_id': oura_user_id,
        }

    )

    create_webhook_subscription(token_data['access_token'])
    return JsonResponse({"success": True, "message": "Oura connected!"})


##Helper function to add workouts
def sync_oura_for_user(user):

    try:
        connection = WearableConnection.objects.get(
            user=user,
            device_type='oura',
            is_active=True
        )
    
    except WearableConnection.DoesNotExist:
        raise Exception("Oura Not Connected")
    
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=7)

    headers = {'Authorization': f'Bearer {connection.access_token}'}

    response = requests.get(
        f'https://api.ouraring.com/v2/usercollection/workout?start_date={start_date}&end_date={end_date}',
        headers=headers
    )

    if response.status_code != 200:
        raise Exception("Failed to fetch Oura data")
    
    data = response.json()
    workouts_added = 0

    for workout in data.get('data', []):
        activity_type = workout.get('activity', 'Unknown Activity')
        _, created = Cardio.objects.get_or_create(
            user = user,
            activity=f"Oura: {activity_type}",
            date=workout.get('start_datetime'),
            defaults={
                'duration': workout.get('duration', 0) // 60
            }
        )
        if created: workouts_added += 1

    connection.last_sync = datetime.now()
    connection.save()

    return {"success": True, "workouts_added": workouts_added}





@csrf_exempt
def sync_oura(request):
    user = get_user_from_token(request)
    
    if not user:
        return JsonResponse({'error': 'Authentication Required'}, status=401)
    
    try:
        result = sync_oura_for_user(user)
        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)
    

#Oura Webhook function

@csrf_exempt
def oura_webhook(request):

    signature = request.headers.get('X-Oura-Signature')
    if signature and OURA_WEBHOOK_SECRET:
        expected_signature = hmac.new(
            OURA_WEBHOOK_SECRET.encode(),
            request.body,
            hashlib.sha256 #uses sha hashing algorith
        ).hexdigest() #converts hash into readable hexadecimal string

        if signature != expected_signature:
            return JsonResponse({'error': 'Invalid Signature'}, status = 401)
        
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status = 400)
    
    event_type = data.get('event_type')
    oura_user_id = data.get('user_id')

    try: connection = WearableConnection.objects.get(
        external_user_id=oura_user_id,
        device_type='oura',
        is_active=True
    )
    
    except WearableConnection.DoesNotExist:
        return JsonResponse({'error': 'User Not Found'}, status = 404)
    
    if event_type == 'workout.created':
        try:
            sync_oura_for_user(connection.user)
        except Exception as e: # stores error in e
            return JsonResponse({'error': str(e)}, status = 500)
        
    return JsonResponse({'status': 'success'})


def create_webhook_subscription(user_access_token):
    response = requests.post(
        'https://api.ouraring.com/v2/webhook/subscription',
        headers={'Authorization': f'Bearer {user_access_token}'},
        json={
            'callback_url': f'{OURA_REDIRECT_URI.rsplit("/", 2)[0]}/webhook/',
            'verification_token': OURA_WEBHOOK_SECRET,
            'event_type': 'create',
            'data_type': 'workout',
        }
    )

    if response.status_code == 200:
        return response.json()
        
    else:
        print(f"Failed to create webhook subscription: {response.status_code} - {response.text}")
        return None



#############STRAVA######################################


@csrf_exempt
def strava_connect(request):
   
    user = get_user_from_token(request)

    if not user:
        return JsonResponse({'error': 'Authentication Required'}, status = 401)
    
    auth_url = (
        f"https://www.strava.com/oauth/authorize?"
        f"client_id={STRAVA_CLIENT_ID}&"
        f"redirect_uri={STRAVA_REDIRECT_URI}&"
        f"response_type=code&"
        f"scope=activity:read_all&"
        f"state={user.id}"
    )

    return JsonResponse({"auth_url": auth_url})


@csrf_exempt
def strava_callback(request):

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
        'https://www.strava.com/oauth/token',
        data={
            'grant_type': 'authorization_code',  
            'code': code,                         # The code Oura sent us
            'redirect_uri': STRAVA_REDIRECT_URI,   
            'client_id': STRAVA_CLIENT_ID,        
            'client_secret': STRAVA_CLIENT_SECRET,
        }
    )

    if token_response.status_code != 200:
        return JsonResponse({"error": "Failed to exchange token"}, status = 400)

    token_data = token_response.json()

    strava_athlete_id = str(token_data.get('athlete', {}))

# updates if finds user+oura, creates if does not find
    WearableConnection.objects.update_or_create(
        user = user,
        device_type = 'strava',
        defaults = {
            'access_token': token_data['access_token'],
            'refresh_token': token_data['refresh_token'],
            'expires_at': datetime.now() + timedelta(seconds = token_data['expires_in']),
            'is_active': True,
            'external_user_id': strava_athlete_id,
        }

    )

    create_webhook_subscription(token_data['access_token'])
    return JsonResponse({"success": True, "message": "Strava connected!"})


##Helper function to add workouts
def sync_strava_for_user(user):

    try:
        connection = WearableConnection.objects.get(
            user=user,
            device_type='oura',
            is_active=True
        )
    
    except WearableConnection.DoesNotExist:
        raise Exception("Oura Not Connected")
    
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=7)

    headers = {'Authorization': f'Bearer {connection.access_token}'}

    response = requests.get(
        f'https://api.ouraring.com/v2/usercollection/workout?start_date={start_date}&end_date={end_date}',
        headers=headers
    )

    if response.status_code != 200:
        raise Exception("Failed to fetch Oura data")
    
    data = response.json()
    workouts_added = 0

    for workout in data.get('data', []):
        activity_type = workout.get('activity', 'Unknown Activity')
        _, created = Cardio.objects.get_or_create(
            user = user,
            activity=f"Oura: {activity_type}",
            date=workout.get('start_datetime'),
            defaults={
                'duration': workout.get('duration', 0) // 60
            }
        )
        if created: workouts_added += 1

    connection.last_sync = datetime.now()
    connection.save()

    return {"success": True, "workouts_added": workouts_added}



