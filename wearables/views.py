from venv import logger
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
from django.utils.dateparse import parse_datetime
import secrets


OURA_CLIENT_ID = os.environ.get('OURA_CLIENT_ID')
OURA_CLIENT_SECRET = os.environ.get('OURA_CLIENT_SECRET')
OURA_REDIRECT_URI = os.environ.get('OURA_REDIRECT_URI')
OURA_WEBHOOK_SECRET = os.environ.get('OURA_WEBHOOK_SECRET')


STRAVA_CLIENT_ID = os.environ.get('STRAVA_CLIENT_ID')
STRAVA_CLIENT_SECRET = os.environ.get('STRAVA_CLIENT_SECRET')
STRAVA_REDIRECT_URI = os.environ.get('STRAVA_REDIRECT_URI')


WHOOP_CLIENT_ID = os.environ.get('WHOOP_CLIENT_ID')
WHOOP_CLIENT_SECRET = os.environ.get('WHOOP_CLIENT_SECRET')
WHOOP_REDIRECT_URI = 'https://fitnesswebsitebackend-production.up.railway.app/api/wearables/whoop/callback/'

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

    return redirect('https://www.rhetts-fitness-community.com/connect')
    

##Helper function to add workouts
def sync_oura_for_user(user, days_back=7):
    try:
        connection = WearableConnection.objects.get(
            user=user,
            device_type='oura',
            is_active=True
        )
    except WearableConnection.DoesNotExist:
        raise Exception("Oura Not Connected")
    
    # REFRESH TOKEN IF EXPIRED
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
    
    end_date = timezone.now().date()
    start_date = end_date - timedelta(days=days_back)
    
    print(f"Syncing Oura for user {user.id} from {start_date} to {end_date}")

    headers = {'Authorization': f'Bearer {connection.access_token}'}

    url = f'https://api.ouraring.com/v2/usercollection/workout?start_date={start_date}&end_date={end_date}'
    print(f"Oura API URL: {url}")
    
    response = requests.get(url, headers=headers)

    print(f"Oura API response status: {response.status_code}")
    print(f"Oura API response: {response.text}")

    if response.status_code != 200:
        raise Exception(f"Failed to fetch Oura data: {response.status_code} - {response.text}")
    
    data = response.json()
    workouts_added = 0

    for workout in data.get('data', []):
        activity_type = workout.get('activity', 'Unknown Activity')
        oura_workout_id = str(workout.get('id'))

        duration_seconds = workout.get('duration', 0)

        if not duration_seconds:
            start = workout.get('start_datetime')
            end = workout.get('end_datetime')

            if start and end:
                start_dt = parse_datetime(start)
                end_dt = parse_datetime(end)
                duration_seconds = int((end_dt - start_dt).total_seconds())
            else:
                duration_seconds = 0

            duration_minutes = max(duration_seconds // 60, 1)
        else:
            duration_minutes = max(duration_seconds // 60, 1)
    
        _, created = Cardio.objects.get_or_create(
            user=user,
            external_id=f"oura_{oura_workout_id}",
            defaults={
                'activity': f"Oura: {activity_type}",
                'date': workout.get('start_datetime'),
                'duration': duration_minutes
            }
        )
        if created: 
            workouts_added += 1

    connection.last_sync = timezone.now()
    connection.save()

    print(f"Oura sync complete: {workouts_added} workouts added")
    return {"success": True, "workouts_added": workouts_added}
#Oura Webhook function

@csrf_exempt
def sync_oura(request):
    user = get_user_from_token(request)
    
    if not user:
        return JsonResponse({'error': 'Authentication Required'}, status=401)
    
    try:
        result = sync_oura_for_user(user, days_back=3)
        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)

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
            sync_oura_for_user(connection.user, days_back=1)
        except Exception as e: # stores error in e
            return JsonResponse({'error': str(e)}, status = 500)
        
    return JsonResponse({'status': 'success'})


####debugging##################

def create_webhook_subscription(user_access_token):
    webhook_url = 'https://fitnesswebsitebackend-production.up.railway.app/api/wearables/oura/webhook/'
    
    print(f"Attempting to create webhook with URL: {webhook_url}")
    print(f"Using verification token: {OURA_WEBHOOK_SECRET}")

    response = requests.post(
        'https://api.ouraring.com/v2/webhook/subscription',
        headers={
            'Authorization': f'Bearer {user_access_token}',
            'Content-Type': 'application/json'
        },
        json={
            'callback_url': webhook_url,
            'verification_token': OURA_WEBHOOK_SECRET,
            'event_type': 'workout.create',  # Specify exact event type
            'data_type': 'workout',
        }
    )

    print(f"Webhook creation response status: {response.status_code}")
    print(f"Webhook creation response body: {response.text}")

    if response.status_code == 200:
        print("Webhook subscription successful")
        return response.json()
    else:
        print(f"Webhook subscription failed: {response.status_code}")
        print(f"Response body: {response.text}")
        return None
    

@csrf_exempt
def oura_disconnect(request):
    user = get_user_from_token(request)
    
    if not user:
        return JsonResponse({'error': 'Authentication Required'}, status=401)
    
    try:
        # Get all Oura connections for this user and deactivate them
        connections = WearableConnection.objects.filter(
            user=user,
            device_type='oura',
            is_active=True
        )
        
        count = connections.count()
        connections.delete()
        
        return JsonResponse({
            'success': True, 
            'message': f'Disconnected {count} Oura connection(s)'
        })
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)
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

    return redirect('https://www.rhetts-fitness-community.com/connect')


##Helper function to add workouts
def sync_strava_for_user(user, days_back=30):
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
    
    # Use the days_back parameter
    end_date = int(timezone.now().timestamp())
    start_date = int((timezone.now() - timedelta(days=days_back)).timestamp())

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
        result = sync_strava_for_user(user, days_back=2)
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
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    
    object_type = data.get('object_type')
    aspect_type = data.get('aspect_type')
    athlete_id = str(data.get('owner_id'))
    
    print(f"Strava webhook received: {object_type}/{aspect_type} for athlete {athlete_id}")

    if object_type == 'activity' and aspect_type == 'create':
        try: 
            # Get the ACTIVE connection (use filter + first instead of get)
            connection = WearableConnection.objects.filter(
                external_user_id=athlete_id,
                device_type='strava',
                is_active=True
            ).order_by('-id').first()  # Get the most recent one
        
            if not connection:
                print(f"No active connection found for athlete {athlete_id}")
                return JsonResponse({'error': 'User Not Found'}, status=404)
        
            print(f"Found connection for athlete {athlete_id}, user {connection.user.id}")
        
            # Try to sync
            result = sync_strava_for_user(connection.user, days_back=1)
            print(f"Sync successful: {result}")
            return JsonResponse({'status': 'success', 'result': result})

        except Exception as e:
            # Log the full error
            import traceback
            error_msg = f"Error syncing Strava for athlete {athlete_id}: {str(e)}"
            print(error_msg)
            print(traceback.format_exc())
            return JsonResponse({'error': str(e), 'traceback': traceback.format_exc()}, status=500)
    
    return JsonResponse({'status': 'success'})

@csrf_exempt
def strava_disconnect(request):
    user = get_user_from_token(request)
    
    if not user:
        return JsonResponse({'error': 'Authentication Required'}, status=401)
    
    try:
        # Get all Strava connections for this user and deactivate them
        connections = WearableConnection.objects.filter(
            user=user,
            device_type='strava',
            is_active=True
        )
        
        count = connections.count()
        connections.delete()
        
        return JsonResponse({
            'success': True, 
            'message': f'Disconnected {count} Strava connection(s)'
        })
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)
    


    ################WHOOP###################################

####Automatic Adding Workouts ###
def sync_whoop_for_user(user,days_back=30):
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
    end_time = timezone.now()
    start_time = end_time - timedelta(days=days_back)

    end_date = end_time.strftime('%Y-%m-%dT%H:%M:%S.000Z')
    start_date = start_time.strftime('%Y-%m-%dT%H:%M:%S.000Z')
    
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
        result = sync_whoop_for_user(user, days_back=3)
        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)




@csrf_exempt
def whoop_connect(request):
    """Initiate Whoop OAuth connection process"""
    user = get_user_from_token(request)
    
    if not user:
        return JsonResponse({'error': 'Authentication Required'}, status=401)
    
    # Generate a secure state parameter that includes user ID
    state = f"{secrets.token_urlsafe(16)}_{user.id}"
    
    auth_url = (
        f"https://api.prod.whoop.com/oauth/oauth2/auth?"
        f"client_id={WHOOP_CLIENT_ID}&"
        f"redirect_uri={WHOOP_REDIRECT_URI}&"
        f"response_type=code&"
        f"scope=read:workout&"
        f"state={state}"
    )
    
    return JsonResponse({"auth_url": auth_url})

@csrf_exempt
def whoop_callback(request):
    """Handle Whoop OAuth callback"""
    logger.info("Whoop Callback Initiated")
    
    # Check for OAuth errors first
    error = request.GET.get('error')
    if error:
        logger.error(f"OAuth Error: {error}")
        logger.error(f"Error Description: {request.GET.get('error_description')}")
        return JsonResponse({"error": "OAuth Error", "details": dict(request.GET)}, status=400)
    
    # Extract code and state
    code = request.GET.get('code')
    state = request.GET.get('state')
    
    if not code or not state:
        logger.error("Invalid callback: Missing code or state")
        return JsonResponse({
            "error": "Invalid callback", 
            "details": {
                "code_present": bool(code),
                "state_present": bool(state)
            }
        }, status=400)
    
    # Securely extract user ID from state
    try:
        parts = state.split('_')
        if len(parts) < 2:
            logger.error("Invalid state parameter")
            return JsonResponse({"error": "Invalid state parameter"}, status=400)
        
        user_id = parts[-1]
        user = User.objects.get(id=user_id)
        logger.info(f"User identified: {user.username}")
    except User.DoesNotExist:
        logger.error(f"User not found for ID: {user_id}")
        return JsonResponse({"error": "User not found"}, status=404)
    
    # Token exchange
    try:
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
        
        logger.info(f"Token Exchange Response Status: {token_response.status_code}")
        
        if token_response.status_code != 200:
            logger.error(f"Token exchange failed: {token_response.text}")
            return JsonResponse({
                "error": "Failed to exchange token", 
                "status_code": token_response.status_code,
                "response_body": token_response.text
            }, status=400)
        
        token_data = token_response.json()
        
        # User info retrieval
        headers = {
            'Authorization': f'Bearer {token_data["access_token"]}',
            'Accept': 'application/json'
        }
        
        user_info_response = requests.get(
            'https://api.prod.whoop.com/developer/v1/user/profile/basic',
            headers=headers,
            timeout=10
        )
        
        logger.info(f"User Info Response Status: {user_info_response.status_code}")
        
        if user_info_response.status_code == 200:
            user_info = user_info_response.json()
            whoop_user_id = str(user_info.get('user_id'))
        else:
            # Fallback to generating a unique identifier
            whoop_user_id = token_data.get('access_token')[-10:]
        
        # Save or update connection
        connection, created = WearableConnection.objects.update_or_create(
            user=user,
            device_type='whoop',
            defaults={
                'access_token': token_data['access_token'],
                'refresh_token': token_data.get('refresh_token', ''),
                'expires_at': timezone.now() + timedelta(seconds=token_data.get('expires_in', 3600)),
                'is_active': True,
                'external_user_id': whoop_user_id,
            }
        )
        
        logger.info(f"Whoop connection {'created' if created else 'updated'}")
        
        return redirect('https://www.rhetts-fitness-community.com/connect')
    
    except requests.RequestException as req_err:
        logger.error(f"Request Error: {req_err}")
        return JsonResponse({
            "error": "Network error during connection", 
            "details": str(req_err)
        }, status=500)
    
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        return JsonResponse({
            "error": "Unexpected error during connection", 
            "details": str(e)
        }, status=500)

@csrf_exempt
def whoop_webhook(request):
    """Handle incoming Whoop webhook notifications"""
    logger.info("Whoop Webhook Received")
    
    try:
        data = json.loads(request.body)
        event_type = data.get('type')
        whoop_user_id = str(data.get('user_id'))
        
        logger.info(f"Webhook Event: {event_type}, User ID: {whoop_user_id}")
        
        if event_type and 'workout' in event_type:
            try:
                connection = WearableConnection.objects.get(
                    external_user_id=whoop_user_id,
                    device_type='whoop',
                    is_active=True
                )
                
                sync_result = sync_whoop_for_user(connection.user, days_back=1)
                logger.info(f"Sync Result: {sync_result}")
                
                return JsonResponse({'status': 'success', 'result': sync_result})
            
            except WearableConnection.DoesNotExist:
                logger.warning(f"No active connection for Whoop user {whoop_user_id}")
                return JsonResponse({'error': 'User not found'}, status=404)
            
            except Exception as sync_error:
                logger.error(f"Sync Error: {sync_error}", exc_info=True)
                return JsonResponse({'error': str(sync_error)}, status=500)
        
        return JsonResponse({'status': 'success'})
    
    except json.JSONDecodeError:
        logger.error("Invalid JSON in Whoop webhook")
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    
    except Exception as e:
        logger.error(f"Unexpected Webhook Error: {e}", exc_info=True)
        return JsonResponse({'error': str(e)}, status=500)

@csrf_exempt
def whoop_disconnect(request):
    """Disconnect Whoop connection for the current user"""
    user = get_user_from_token(request)
    
    if not user:
        return JsonResponse({'error': 'Authentication Required'}, status=401)
    
    try:
        connections = WearableConnection.objects.filter(
            user=user,
            device_type='whoop',
            is_active=True
        )
        
        count = connections.count()
        connections.delete()
        
        logger.info(f"Disconnected {count} Whoop connection(s) for user {user.username}")
        
        return JsonResponse({
            'success': True, 
            'message': f'Disconnected {count} Whoop connection(s)'
        })
    
    except Exception as e:
        logger.error(f"Disconnect Error: {e}", exc_info=True)
        return JsonResponse({'error': str(e)}, status=400)


######CHECK CONNECTION#######

@csrf_exempt
def check_connection_status(request):
    user = get_user_from_token(request)
    
    if not user:
        return JsonResponse({'error': 'Authentication Required'}, status=401)
    
    # Check which devices are connected
    oura_connected = WearableConnection.objects.filter(
        user=user,
        device_type='oura',
        is_active=True
    ).exists()
    
    strava_connected = WearableConnection.objects.filter(
        user=user,
        device_type='strava',
        is_active=True
    ).exists()
    
    whoop_connected = WearableConnection.objects.filter(
        user=user,
        device_type='whoop',
        is_active=True
    ).exists()
    
    return JsonResponse({
        'oura': oura_connected,
        'strava': strava_connected,
        'whoop': whoop_connected,
    })


