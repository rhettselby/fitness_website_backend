from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from rest_framework_simplejwt.tokens import AccessToken
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from django.contrib.auth.models import User
import os
from datetime import timedelta
from .models import WearableConnection
from fitness.models import Cardio
import requests
import hashlib
import hmac
import json
from django.shortcuts import redirect
from django.utils import timezone


OURA_CLIENT_ID = os.environ.get('OURA_CLIENT_ID')
OURA_CLIENT_SECRET = os.environ.get('OURA_CLIENT_SECRET')
OURA_REDIRECT_URI = os.environ.get('OURA_REDIRECT_URI')
OURA_WEBHOOK_SECRET = os.environ.get('OURA_WEBHOOK_SECRET')


STRAVA_CLIENT_ID = os.environ.get('STRAVA_CLIENT_ID')
STRAVA_CLIENT_SECRET = os.environ.get('STRAVA_CLIENT_SECRET')
STRAVA_REDIRECT_URI = os.environ.get('STRAVA_REDIRECT_URI')


WHOOP_CLIENT_ID = os.environ.get('WHOOP_CLIENT_ID')
WHOOP_CLIENT_SECRET = os.environ.get('WHOOP_CLIENT_SECRET')
WHOOP_REDIRECT_URI = os.environ.get('WHOOP_REDIRECT_URI')

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

    code = request.GET.get('code')
    user_id = request.GET.get('state')

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
            'expires_at': timezone.now() + timedelta(seconds = token_data['expires_in']),
            'is_active': True,
            'external_user_id': oura_user_id,
        }

    )

    create_webhook_subscription(token_data['access_token'])
    return redirect('https://fitness-website-delta-blush.vercel.app/profile')


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
    
    # REFRESH TOKEN IF EXPIRED (Oura tokens last longer but still good to check)
    if connection.expires_at and timezone.now() >= connection.expires_at:
        print(f"Refreshing expired Oura token for user {user.id}")
        token_response = requests.post(
            'https://api.ouraring.com/oauth/token',
            data={
                'grant_type': 'refresh_token',
                'refresh_token': connection.refresh_token,
                'client_id': OURA_CLIENT_ID,
                'client_secret': OURA_CLIENT_SECRET,
            }
        )
        
        if token_response.status_code == 200:
            token_data = token_response.json()
            connection.access_token = token_data['access_token']
            connection.refresh_token = token_data['refresh_token']
            connection.expires_at = timezone.now() + timedelta(seconds=token_data['expires_in'])
            connection.save()
            print(f"Oura token refreshed successfully for user {user.id}")
        else:
            print(f"Failed to refresh Oura token: {token_response.status_code} - {token_response.text}")
            raise Exception("Failed to refresh Oura token")
    
    # Rest of your existing Oura sync code...
    end_date = timezone.now().date()
    start_date = end_date - timedelta(days=7)

    headers = {'Authorization': f'Bearer {connection.access_token}'}

    response = requests.get(
        f'https://api.ouraring.com/v2/usercollection/workout?start_date={start_date}&end_date={end_date}',
        headers=headers
    )

    if response.status_code != 200:
        print(f"Failed to fetch Oura workouts: {response.status_code} - {response.text}")
        raise Exception(f"Failed to fetch Oura data: {response.status_code}")
    
    data = response.json()
    workouts_added = 0

    for workout in data.get('data', []):
        activity_type = workout.get('activity', 'Unknown Activity')
        oura_workout_id = str(workout.get('id'))
    
        _, created = Cardio.objects.get_or_create(
            user=user,
            external_id=f"oura_{oura_workout_id}",
            defaults={
                'activity': f"Oura: {activity_type}",
                'date': workout.get('start_datetime'),
                'duration': workout.get('duration', 0) // 60
            }
        )
        if created: 
            workouts_added += 1

    connection.last_sync = timezone.now()
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

    code = request.GET.get('code')
    user_id = request.GET.get('state')

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
            'code': code,                     
            'client_id': STRAVA_CLIENT_ID,        
            'client_secret': STRAVA_CLIENT_SECRET,
        }
    )

    if token_response.status_code != 200:
        return JsonResponse({"error": "Failed to exchange token"}, status = 400)

    token_data = token_response.json()

    strava_athlete_id = str(token_data.get('athlete', {}).get('id'))

# updates if finds user+oura, creates if does not find
    WearableConnection.objects.update_or_create(
        user = user,
        device_type = 'strava',
        defaults = {
            'access_token': token_data['access_token'],
            'refresh_token': token_data['refresh_token'],
            'expires_at': timezone.now() + timedelta(seconds = token_data['expires_in']),
            'is_active': True,
            'external_user_id': strava_athlete_id,
        }

    )

    return redirect('https://fitness-website-delta-blush.vercel.app/profile')


##Helper function to add workouts
def sync_strava_for_user(user):
    try:
        connection = WearableConnection.objects.get(
            user=user,
            device_type='strava',
            is_active=True
        )
    except WearableConnection.DoesNotExist:
        raise Exception("Strava Not Connected")
    
    # REFRESH TOKEN IF EXPIRED
    if connection.expires_at and timezone.now() >= connection.expires_at:
        print(f"Refreshing expired Strava token for user {user.id}")
        token_response = requests.post(
            'https://www.strava.com/oauth/token',
            data={
                'client_id': STRAVA_CLIENT_ID,
                'client_secret': STRAVA_CLIENT_SECRET,
                'grant_type': 'refresh_token',
                'refresh_token': connection.refresh_token,
            }
        )
        
        if token_response.status_code == 200:
            token_data = token_response.json()
            connection.access_token = token_data['access_token']
            connection.refresh_token = token_data['refresh_token']
            connection.expires_at = timezone.now() + timedelta(seconds=token_data['expires_in'])
            connection.save()
            print(f"Token refreshed successfully for user {user.id}")
        else:
            print(f"Failed to refresh token: {token_response.status_code} - {token_response.text}")
            raise Exception("Failed to refresh Strava token")
    
    # NOW sync with fresh token
    end_date = int(timezone.now().timestamp())
    start_date = int((timezone.now() - timedelta(days=30)).timestamp())

    headers = {'Authorization': f'Bearer {connection.access_token}'}

    response = requests.get(
        f'https://www.strava.com/api/v3/athlete/activities?after={start_date}&before={end_date}',
        headers=headers
    )

    if response.status_code != 200:
        print(f"Failed to fetch Strava activities: {response.status_code} - {response.text}")
        raise Exception(f"Failed to fetch Strava data: {response.status_code}")
    
    activities = response.json()
    workouts_added = 0

    for activity in activities:
        activity_type = activity.get('type', 'Unknown Activity')
        activity_date = activity.get('start_date')
        strava_activity_id = str(activity.get('id'))
    
        _, created = Cardio.objects.get_or_create(
            user=user,
            external_id=f"strava_{strava_activity_id}",
            defaults={
                'activity': f"Strava: {activity_type}",
                'date': activity_date,
                'duration': activity.get('moving_time', 0) // 60
            }
        )
        if created:
            workouts_added += 1

    connection.last_sync = timezone.now()
    connection.save()

    return {"success": True, "workouts_added": workouts_added}

@csrf_exempt
def sync_strava(request):
    user = get_user_from_token(request)
    
    if not user:
        return JsonResponse({'error': 'Authentication Required'}, status=401)
    
    try:
        result = sync_strava_for_user(user)
        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@csrf_exempt
def strava_webhook(request):

    if request.method == "GET":
        challenge = request.GET.get('hub.challenge')
        return JsonResponse({'hub.challenge': challenge})
    
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status = 400)

    
    object_type = data.get('object_type')
    aspect_type = data.get('aspect_type')
    athlete_id = str(data.get('owner_id'))


    if object_type == 'activity' and aspect_type == 'create':
        try: 
            connection = WearableConnection.objects.get(
                external_user_id=athlete_id,
                device_type='strava',
                is_active=True
            )
            sync_strava_for_user(connection.user)
    
        except WearableConnection.DoesNotExist:
            return JsonResponse({'error': 'User Not Found'}, status = 404)
    
        except Exception as e:
            return JsonResponse({'error': str(e)}, status = 500)
        
    return JsonResponse({'status': 'success'})



def create_strava_webhook_subscription(access_token=None):
#strava webhooks are app wide, not per user like Oura
    
    response = requests.post(
        'https://www.strava.com/api/v3/push_subscriptions',
        data={
            'client_id': STRAVA_CLIENT_ID,
            'client_secret': STRAVA_CLIENT_SECRET,
            'callback_url': f'{STRAVA_REDIRECT_URI.rsplit("/", 2)[0]}/webhook/',
            'verify_token': 'STRAVA_WEBHOOK_VERIFY'
        }
    )
    
    if response.status_code in [200, 201]:
        return response.json()
    else:
        print(f"Failed to create Strava webhook: {response.status_code} - {response.text}")
        return None
    



    ################WHOOP###################################

@csrf_exempt
def whoop_connect(request):
    user = get_user_from_token(request)
    
    if not user:
        return JsonResponse({'error': 'Authentication Required'}, status=401)
    
    auth_url = (
        f"https://api.prod.whoop.com/oauth/oauth2/auth?"
        f"client_id={WHOOP_CLIENT_ID}&"
        f"redirect_uri={WHOOP_REDIRECT_URI}&"
        f"response_type=code&"
        f"scope=read:workout read:recovery read:sleep&"
        f"state={user.id}"
    )
    
    return JsonResponse({"auth_url": auth_url})



@csrf_exempt
def whoop_callback(request):
    code = request.GET.get('code')
    user_id = request.GET.get('state')
    
    if not code or not user_id:
        return JsonResponse({"error": "Invalid callback"}, status=400)
    
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return JsonResponse({"error": "User Not Found"}, status=404)
    
    # Exchange authorization code for access token
    token_response = requests.post(
        'https://api.prod.whoop.com/oauth/oauth2/token',
        data={
            'grant_type': 'authorization_code',
            'code': code,
            'client_id': WHOOP_CLIENT_ID,
            'client_secret': WHOOP_CLIENT_SECRET,
            'redirect_uri': WHOOP_REDIRECT_URI,
        }
    )
    
    if token_response.status_code != 200:
        return JsonResponse({"error": "Failed to exchange token"}, status=400)
    
    token_data = token_response.json()
    
    # Get Whoop user info
    headers = {'Authorization': f'Bearer {token_data["access_token"]}'}
    user_info_response = requests.get(
        'https://api.prod.whoop.com/developer/v1/user/profile/basic',
        headers=headers
    )
    
    whoop_user_id = None
    if user_info_response.status_code == 200:
        whoop_user_id = str(user_info_response.json().get('user_id'))
    
    # Save connection
    WearableConnection.objects.update_or_create(
        user=user,
        device_type='whoop',
        defaults={
            'access_token': token_data['access_token'],
            'refresh_token': token_data['refresh_token'],
            'expires_at': timezone.now() + timedelta(seconds=token_data['expires_in']),
            'is_active': True,
            'external_user_id': whoop_user_id,
        }
    )
    
    return redirect('https://fitness-website-delta-blush.vercel.app/profile')


####Automatic Adding Workouts ###
def sync_whoop_for_user(user):
    """Helper function to sync Whoop data for a specific user"""
    try:
        connection = WearableConnection.objects.get(
            user=user,
            device_type='whoop',
            is_active=True
        )
    except WearableConnection.DoesNotExist:
        raise Exception("Whoop not connected")
    
    # REFRESH TOKEN IF EXPIRED
    if connection.expires_at and timezone.now() >= connection.expires_at:
        print(f"Refreshing expired Whoop token for user {user.id}")
        token_response = requests.post(
            'https://api.prod.whoop.com/oauth/oauth2/token',
            data={
                'grant_type': 'refresh_token',
                'refresh_token': connection.refresh_token,
                'client_id': WHOOP_CLIENT_ID,
                'client_secret': WHOOP_CLIENT_SECRET,
            }
        )
        
        if token_response.status_code == 200:
            token_data = token_response.json()
            connection.access_token = token_data['access_token']
            connection.refresh_token = token_data['refresh_token']
            connection.expires_at = timezone.now() + timedelta(seconds=token_data['expires_in'])
            connection.save()
            print(f"Whoop token refreshed successfully for user {user.id}")
        else:
            print(f"Failed to refresh Whoop token: {token_response.status_code} - {token_response.text}")
            raise Exception("Failed to refresh Whoop token")
    
    # Whoop uses ISO format dates
    end_date = timezone.now().strftime('%Y-%m-%dT%H:%M:%S.000Z')
    start_date = (timezone.now() - timedelta(days=30)).strftime('%Y-%m-%dT%H:%M:%S.000Z')
    
    headers = {'Authorization': f'Bearer {connection.access_token}'}
    
    response = requests.get(
        f'https://api.prod.whoop.com/developer/v1/activity/workout?start={start_date}&end={end_date}',
        headers=headers
    )
    
    if response.status_code != 200:
        print(f"Failed to fetch Whoop workouts: {response.status_code} - {response.text}")
        raise Exception(f"Failed to fetch Whoop data: {response.status_code}")
    
    data = response.json()
    workouts_added = 0
    
    for workout in data.get('records', []):
        sport_name = workout.get('sport_id', 'Unknown Activity')
        whoop_workout_id = str(workout.get('id'))
        workout_start = workout.get('start')
        
        _, created = Cardio.objects.get_or_create(
            user=user,
            external_id=f"whoop_{whoop_workout_id}",
            defaults={
                'activity': f"Whoop: {sport_name}",
                'date': workout_start,
                'duration': workout.get('score', {}).get('strain', 0)
            }
        )
        if created:
            workouts_added += 1
    
    connection.last_sync = timezone.now()
    connection.save()
    
    return {"success": True, "workouts_added": workouts_added}

@csrf_exempt
def sync_whoop(request):
    user = get_user_from_token(request)
    
    if not user:
        return JsonResponse({'error': 'Authentication Required'}, status=401)
    
    try:
        result = sync_whoop_for_user(user)
        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)
    

@csrf_exempt
def whoop_webhook(request):
    """Handle incoming Whoop webhook notifications"""
    
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    
    # Extract event info
    event_type = data.get('type')  # e.g., "workout.updated"
    whoop_user_id = str(data.get('user_id'))
    
    # Only process workout events
    if event_type and 'workout' in event_type:
        try:
            connection = WearableConnection.objects.get(
                external_user_id=whoop_user_id,
                device_type='whoop',
                is_active=True
            )
            sync_whoop_for_user(connection.user)
        except WearableConnection.DoesNotExist:
            return JsonResponse({'error': 'User not found'}, status=404)
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)
    
    return JsonResponse({'status': 'success'})