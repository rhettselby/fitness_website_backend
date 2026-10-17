import base64
import logging
logger = logging.getLogger(__name__)
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from rest_framework_simplejwt.tokens import AccessToken
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from django.contrib.auth.models import User
from django.db import transaction
import os
from datetime import timedelta
from .models import WearableConnection
from fitness.models import Cardio, Sport
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


##### OAuth token refresh (shared by Oura / Strava / Whoop) #####
#
# All three providers rotate refresh tokens: every refresh returns a new refresh_token and
# invalidates the previous one, and Whoop (ORY Hydra) treats re-use of an old one as theft
# and revokes the whole grant. Two things went wrong with the old inline refresh blocks:
#   * any non-200 from the token endpoint was treated as final. Whoop's endpoint returns
#     transient 502s / very slow responses around hourly boundaries and Oura's 504s at
#     00:00 UTC, so a single blip permanently deactivated the connection (Whoop) or left it
#     retrying a dead token every 15 minutes forever (Oura). Now only a run of definitive
#     OAuth rejections deactivates; transient failures simply retry on the next sync.
#   * `connection.save()` wrote every column, so an overlapping sync (webhook in the web
#     process + periodic task in the worker) could clobber freshly rotated tokens with
#     stale ones. The refresh now runs under a row lock and saves only the token fields.

REQUEST_TIMEOUT = (5, 20)                       # (connect, read) seconds for provider data calls
# Token endpoints get more read headroom: Whoop's has been measured at 6-18s around the
# hour, and giving up while it is mid-rotation is exactly how a refresh token gets desynced.
TOKEN_REQUEST_TIMEOUT = (5, 40)
REFRESH_EXPIRY_BUFFER = timedelta(minutes=2)    # refresh a little early instead of racing expiry
REFRESH_FAILURE_LIMIT = 3                       # consecutive definitive rejections before deactivating

TRANSIENT, CONFIG_ERROR, TOKEN_REJECTED = 'transient', 'config', 'token'


class TokenRefreshError(Exception):
    """Raised when no usable access token could be obtained.

    ``deactivated`` is True when the connection was switched off as a result and the
    user has to re-link the device; otherwise the next sync will simply try again.
    """

    def __init__(self, message, deactivated=False):
        super().__init__(message)
        self.deactivated = deactivated


def token_needs_refresh(connection):
    return bool(connection.expires_at) and \
        timezone.now() >= connection.expires_at - REFRESH_EXPIRY_BUFFER


def mark_token_expired(connection):
    """Force a refresh on the next sync (used when the API rejects a token we thought was valid)."""
    connection.expires_at = timezone.now()
    connection.save(update_fields=['expires_at', 'updated_at'])

def upload_workout(user_id, external_id, data):

    WINDOW = 5
    with transaction.atomic():
        # Blocks other transactions trying to lock this same user until we commit
        User.objects.select_for_update().get(pk=user_id)

        existing = Cardio.objects.filter(
            user_id=user_id,
            start_dt__gte=data.date - WINDOW,
            start_dt__lte=data.date + WINDOW,
        ).first()

        if existing:
            return   # see below
        return Cardio.objects.create(user_id=user_id, **data.fields)


    return



def classify_refresh_failure(response):
    """Sort a non-200 token-endpoint response into TRANSIENT, CONFIG_ERROR or TOKEN_REJECTED.

    TRANSIENT       -> gateway/rate-limit/5xx or a non-JSON body: retry next sync, don't count.
    CONFIG_ERROR    -> the provider rejected OUR client credentials or request shape
                       (invalid_client & co.). Deactivating users would be wrong: fix the app.
    TOKEN_REJECTED  -> a proper OAuth error about the grant itself (invalid_grant,
                       invalid_request for a used/rotated token, Strava's bad refresh_token).
    """
    status = response.status_code
    if status == 429 or status >= 500:
        return TRANSIENT
    try:
        body = response.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        return TRANSIENT            # HTML from a proxy/CDN, not an OAuth answer
    error = str(body.get('error') or '')
    if error in ('invalid_client', 'unauthorized_client', 'invalid_scope', 'unsupported_grant_type'):
        return CONFIG_ERROR
    # Strava: {"message": "Authorization Error", "errors": [{"resource": "Application", "field": "client_id", ...}]}
    for item in body.get('errors') or []:
        if isinstance(item, dict) and item.get('field') in ('client_id', 'client_secret'):
            return CONFIG_ERROR
    return TOKEN_REJECTED


def _notify_reconnect_needed(connection):
    """Queue the 'please re-link your device' email; never let that failing mask the sync error."""
    try:
        from notifications.tasks import send_wearable_reconnect_email
        send_wearable_reconnect_email.delay(connection.user_id, connection.device_type)
    except Exception as exc:
        logger.error("Could not queue reconnect email for user %s (%s): %s",
                     connection.user_id, connection.device_type, exc)


def ensure_fresh_token(connection, token_url, client_id, client_secret, provider_label, extra_data=None):
    """Return ``connection`` with a usable access token, refreshing it if it is (about to be) expired.

    Raises TokenRefreshError when no usable token could be obtained. The returned instance
    may be a different object than the one passed in (the row re-read under lock), so
    callers must use the return value.
    """
    if not token_needs_refresh(connection):
        return connection
    if not client_id or not client_secret:
        raise TokenRefreshError(
            f"{provider_label} client credentials are not configured — check the "
            f"{provider_label.upper()}_CLIENT_ID / _CLIENT_SECRET env vars")

    outcome, detail, deactivated = None, '', False
    with transaction.atomic():
        # Row lock: an overlapping sync (webhook + periodic task) waits here and then finds
        # the token we just stored instead of refreshing (and thereby burning) it again.
        locked = WearableConnection.objects.select_for_update().get(pk=connection.pk)
        if not locked.is_active:
            raise TokenRefreshError(f"{provider_label} connection was deactivated by another sync",
                                    deactivated=True)
        if not token_needs_refresh(locked):
            print(f"{provider_label} token for user {locked.user_id} was already refreshed by another sync")
            return locked

        print(f"Refreshing expired {provider_label} token for user {locked.user_id}")
        data = {
            'grant_type': 'refresh_token',
            'refresh_token': locked.refresh_token,
            'client_id': client_id,
            'client_secret': client_secret,
        }
        if extra_data:
            data.update(extra_data)
        try:
            response = requests.post(
                token_url,
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
                data=data,
                timeout=TOKEN_REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            # Nothing was written; leaving the block rolls back and releases the lock.
            logger.warning("%s token endpoint unreachable for user %s (%s) — will retry next sync",
                           provider_label, locked.user_id, exc)
            raise TokenRefreshError(f"{provider_label} token endpoint unreachable: {exc}") from exc

        if response.status_code == 200:
            token_data = response.json()
            locked.access_token = token_data['access_token']
            # A provider may omit refresh_token when it didn't rotate; keep the current one then.
            locked.refresh_token = token_data.get('refresh_token') or locked.refresh_token
            locked.expires_at = timezone.now() + timedelta(seconds=int(token_data.get('expires_in', 3600)))
            locked.refresh_failures = 0
            locked.save(update_fields=['access_token', 'refresh_token', 'expires_at',
                                       'refresh_failures', 'updated_at'])
            print(f"{provider_label} token refreshed successfully for user {locked.user_id}")
            return locked

        outcome = classify_refresh_failure(response)
        detail = f"{response.status_code} - {response.text[:300]}"
        if outcome == TOKEN_REJECTED:
            locked.refresh_failures += 1
            if locked.refresh_failures >= REFRESH_FAILURE_LIMIT:
                locked.is_active = False
                deactivated = True
            locked.save(update_fields=['refresh_failures', 'is_active', 'updated_at'])
        # Fall out of the block so the counter/deactivation commit before we raise.

    if outcome == TRANSIENT:
        logger.warning("%s token refresh failed transiently for user %s: %s — will retry next sync",
                       provider_label, locked.user_id, detail)
        raise TokenRefreshError(f"{provider_label} token endpoint error {response.status_code} — will retry on next sync")

    if outcome == CONFIG_ERROR:
        logger.error("%s rejected the app's own client credentials/request for user %s: %s — "
                     "check %s_CLIENT_ID / _CLIENT_SECRET; NOT deactivating the connection",
                     provider_label, locked.user_id, detail, provider_label.upper())
        raise TokenRefreshError(f"{provider_label} rejected the app's client credentials ({response.status_code})")

    if deactivated:
        logger.error("%s rejected the refresh token for user %s %d times in a row (%s) — "
                     "connection deactivated, user must reconnect",
                     provider_label, locked.user_id, locked.refresh_failures, detail)
        _notify_reconnect_needed(locked)
        raise TokenRefreshError(
            f"{provider_label} refresh token rejected {REFRESH_FAILURE_LIMIT} times in a row — "
            f"connection deactivated, user must reconnect", deactivated=True)

    logger.error("%s rejected the refresh token for user %s (%d/%d): %s — will retry next sync",
                 provider_label, locked.user_id, locked.refresh_failures, REFRESH_FAILURE_LIMIT, detail)
    raise TokenRefreshError(
        f"{provider_label} rejected the refresh token ({locked.refresh_failures}/{REFRESH_FAILURE_LIMIT}) — "
        f"will retry on next sync")


##### Calculating Points Helper Function #####
# Whoop names are normalised to alphanumerics only, so its "Hiking/Rucking" arrives as
# "hikingrucking" and "Track & Field" as "trackfield" — hence the squashed spellings below.
EXCLUDED_ACTIVITIES = ["walk", "walking", "hike", "hiking", "hikingrucking",
                       # Whoop logs recovery/lifestyle activities as workouts too
                       "sauna", "icebath", "meditation", "gaming"]
CARDIO = ["run", "running", "virtualrun", "trailrun", "cycling", "swimming",
           "yoga", "strength_training", "ride", "rowing", "virtualrow",
           "pilates", "hiit", "stairstepper", "crossfit", "jumprope", "skating", "fitnessclass", "strengthtraining"]
SPEED_INCLUDED = ["run", "running", "virtualrun", "trailrun"]
INTENSITY_INCLUDED = ["running", "cycling", "swimming","yoga", "dance", "strength_training","soccer", "basketball","tennis", "strengthtraining",
           # Whoop sport names where a strain / heart-rate reading is meaningful
           "assaultbike", "australianfootball", "badminton", "barre", "barre3", "barrys",
           "baseball", "boxfitness", "boxing", "circusarts", "climber", "cricket",
           "crosscountryskiing", "duathlon", "elliptical", "f45training", "fencing", "fieldhockey",
           "football", "functionalfitness", "gaelicfootball", "gymnastics", "handball", "hiit",
           "hotyoga", "hurlingcamogie", "icehockey", "iceskating", "inlineskating", "jiujitsu",
           "jumpingrope", "kayaking", "kickboxing", "kiteboarding", "lacrosse", "martialarts",
           "mountainbiking", "netball", "obstaclecourseracing", "paddleboarding", "paddletennis",
           "padel", "paintball", "parkour", "pickleball", "pilates", "polo", "powerlifting",
           "rockclimbing", "rowing", "rugby", "skiing", "snowboarding", "softball", "spikeball",
           "spin", "squash", "stadiumsteps", "stairmaster", "strengthtrainer",
           "strollerjogging", "surfing", "tabletennis", "trackfield", "triathlon", "ultimate",
           "volleyball", "wakeboarding", "waterpolo", "waterskiing", "weightlifting",
           "wheelchairpushing", "wrestling"]

def strava_points(activity, duration, speed):
    score = 100
    if duration < 25:
        return int(50)
    if activity.lower() in CARDIO:
        score += duration
        if activity.lower() in SPEED_INCLUDED:
            #strava lacks intensity, must derive from avg speed
            if speed and (duration > 10):
                mile_minutes = 1609.344 / speed / 60
                if mile_minutes < 9:
                    multiplier = (9 - mile_minutes) ** .25
                    score += (multiplier * score)
    else:
        score = score / 2
    integer_score = int(score)
    return integer_score

def oura_points(activity, duration, intensity):
    score = 100
    if activity.lower() not in EXCLUDED_ACTIVITIES:
        score += duration
    if activity.lower() in INTENSITY_INCLUDED:
        #oura provides intensity (easy, medium, hard)
        if intensity == "moderate":
            score *= 1.6
        elif intensity == "hard":
            score *= 2.9
    if activity.lower() not in CARDIO:
        score = score / 2
    integer_score = int(score)
    return integer_score


def whoop_points(activity, duration, intensity):
    score = 100
    if activity.lower() not in EXCLUDED_ACTIVITIES:
        score += duration
    if activity.lower() in INTENSITY_INCLUDED:
       #whoop gives intensity as strain (0-21), already factoring in duration
       #hence decision to add bonus, instead of multiply
       if intensity:
        score += intensity * 10
    if activity.lower() not in CARDIO:
        score = score / 2
    integer_score = int(score)
    return integer_score


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
            'refresh_failures': 0,
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

    # REFRESH TOKEN IF EXPIRED (shared, lock-protected, tolerant of transient errors)
    connection = ensure_fresh_token(
        connection,
        token_url='https://api.ouraring.com/oauth/token',
        client_id=OURA_CLIENT_ID,
        client_secret=OURA_CLIENT_SECRET,
        provider_label='Oura',
    )

    end_date = (timezone.now() + timedelta(days=1)).date()
    start_date = end_date - timedelta(days=days_back)
    
    print(f"Syncing Oura for user {user.id} from {start_date} to {end_date}")

    headers = {'Authorization': f'Bearer {connection.access_token}'}

    url = f'https://api.ouraring.com/v2/usercollection/workout?start_date={start_date}&end_date={end_date}'
    print(f"Oura API URL: {url}")
    
    response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)

    print(f"Oura API response status: {response.status_code}")
    print(f"Oura API response: {response.text}")

    if response.status_code == 401:
        # Token rejected although expires_at said it was fine (revoked/rotated elsewhere):
        # force a refresh on the next sync rather than trusting expires_at.
        mark_token_expired(connection)
        raise Exception("Failed to fetch Oura data: 401 (token rejected — will refresh on next sync)")
    if response.status_code != 200:
        raise Exception(f"Failed to fetch Oura data: {response.status_code} - {response.text}")
    
    data = response.json()
    workouts_added = 0

    for workout in data.get('data', []):
        activity_type = workout.get('activity', 'Unknown Activity')
        oura_workout_id = str(workout.get('id'))

        start_datetime = workout.get('start_datetime')
        start_dt = parse_datetime(start_datetime)


        start = start_dt
        end = workout.get('end_datetime')

        if start and end:
            end_dt = parse_datetime(end)
            duration_seconds = int((end_dt - start_dt).total_seconds())
        else:
            print("Oura workout duration not found")
            duration_seconds = 0

        duration_minutes = max(duration_seconds // 60, 1)
    
        if duration_minutes < 25:
            continue

          ## Hiking/Walking <45 min doesnt count as workout ;)
        if activity_type == 'walking' or activity_type == 'hiking':
            if duration_minutes < 45:
                print("Skipping oura workout (walk)")
                continue


        ###Logic to prevent duplicate workouts with same start time (different workout id's)
        start_datetime = workout.get('start_datetime')
        
        if activity_type.lower() in CARDIO or activity_type.lower() in EXCLUDED_ACTIVITIES:

            existing_workout = Cardio.objects.filter(
                user=user,
                date=start_datetime
                ).first()

            if existing_workout:
                #if new workout is longer, update existing workout
                if duration_minutes > existing_workout.duration:
                    existing_workout.duration = duration_minutes
                    existing_workout.activity = f"Oura: {activity_type}"
                    existing_workout.external_id = f"oura_{oura_workout_id}"
                    existing_workout.save()
                    print(f"Updated workout to longer duration: {duration_minutes} min")
                    continue
                else:
                    print(f"Skipping shorter duplicate workout")
                    continue
                

            
            intensity = workout.get('intensity', 'easy')

            score = oura_points(activity_type, duration_minutes, intensity)

            created = upload_workout(user,
                external_id = f"oura_{oura_workout_id}",
                defaults={
                    'activity': f"Oura: {activity_type}",
                    'date': workout.get('start_datetime'),
                    'duration': duration_minutes,
                    'score': score,
                }
            )
            if created: 
                workouts_added += 1
                user.profile.score += score
                user.profile.save()
                print(f"User {user} logged {score} points.")
        
        else:
            #sport workout
            existing_workout = Sport.objects.filter(
                user=user,
                date=start_datetime
                ).first()

            if existing_workout:
                #if new workout is longer, update existing workout
                if duration_minutes > existing_workout.duration:
                    existing_workout.duration = duration_minutes
                    existing_workout.sport = f"Oura: {activity_type}"
                    existing_workout.external_id = f"oura_{oura_workout_id}"
                    existing_workout.save()
                    print(f"Updated workout to longer duration: {duration_minutes} min")
                    continue
                else:
                    print(f"Skipping shorter duplicate workout")
                    continue
                

            
            intensity = workout.get('intensity', 'easy')

            score = oura_points(activity_type, duration_minutes, intensity)

            _, created = Sport.objects.get_or_create(
                user=user,
                external_id=f"oura_{oura_workout_id}",
                defaults={
                    'sport': f"Oura: {activity_type}",
                    'date': workout.get('start_datetime'),
                    'duration': duration_minutes,
                    'score': score,
                    'level': "recreational",
                }
            )
            if created: 
                workouts_added += 1
                user.profile.score += score
                user.profile.save()
                print(f"User {user} logged {score} points.")
        

    connection.last_sync = timezone.now()
    # update_fields: never write token columns from a possibly-stale in-memory instance
    connection.save(update_fields=['last_sync', 'updated_at'])

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

    print("Oura webhook initiated")

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

    try: 
        connection = WearableConnection.objects.get(
        external_user_id=oura_user_id,
        device_type='oura',
        is_active=True
    )
        print("oura connection established")
    
    except WearableConnection.DoesNotExist:
        print("Oura webhook failed")
        return JsonResponse({'error': 'User Not Found'}, status = 404)
    
    if event_type == 'workout.created':
        try:
            sync_oura_for_user(connection.user, days_back=1)
            print("sync_oura_for_user called")
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
            'event_type': 'workout.created',  # Specify exact event type
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
            'refresh_failures': 0,
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

    # REFRESH TOKEN IF EXPIRED (shared, lock-protected, tolerant of transient errors)
    connection = ensure_fresh_token(
        connection,
        token_url='https://www.strava.com/oauth/token',
        client_id=STRAVA_CLIENT_ID,
        client_secret=STRAVA_CLIENT_SECRET,
        provider_label='Strava',
    )

    # Use the days_back parameter
    end_date = int(timezone.now().timestamp())
    start_date = int((timezone.now() - timedelta(days=days_back)).timestamp())

    headers = {'Authorization': f'Bearer {connection.access_token}'}

    response = requests.get(
        f'https://www.strava.com/api/v3/athlete/activities?after={start_date}&before={end_date}',
        headers=headers,
        timeout=REQUEST_TIMEOUT,
    )

    if response.status_code == 401:
        mark_token_expired(connection)
        raise Exception("Failed to fetch Strava data: 401 (token rejected — will refresh on next sync)")
    if response.status_code != 200:
        print(f"Failed to fetch Strava activities: {response.status_code} - {response.text}")
        raise Exception(f"Failed to fetch Strava data: {response.status_code}")
    
    activities = response.json()
    workouts_added = 0



    for activity in activities:
        activity_type = activity.get('type', 'Unknown Activity')
        activity_date = activity.get('start_date')
        strava_activity_id = str(activity.get('id'))


        duration_seconds = activity.get('moving_time', 0)


        if not duration_seconds:
            start = activity.get('start_date')
            end = activity.get('end_date')

            if start and end:
                start_dt = parse_datetime(start)
                end_dt = parse_datetime(end)
                duration_seconds = int((end_dt - start_dt).total_seconds())
            else:
                duration_seconds = 0

            duration_minutes = max(duration_seconds // 60, 1)
        else:
            duration_minutes = max(duration_seconds // 60, 1)
        
            #Disabled minimum duration threshold due to popular demand
        if duration_minutes < 10:
            continue

        
            ###Logic to prevent duplicate workouts with same start time (different workout id's)
        start_datetime = activity.get('start_date')

        if activity_type.lower() in CARDIO or activity_type.lower() in EXCLUDED_ACTIVITIES:

            existing_workout = Cardio.objects.filter(
                user=user,
                date=start_datetime
                ).first()

            if existing_workout:
                #if new workout is longer, update existing workout
                if duration_minutes > existing_workout.duration:
                    existing_workout.duration = duration_minutes
                    existing_workout.activity = f"Strava: {activity_type}"
                    existing_workout.external_id = f"Strava_{strava_activity_id}"
                    existing_workout.save()
                    print(f"Updated workout to longer duration: {duration_minutes} min")
                    continue
                else:
                    print(f"Skipping shorter duplicate workout")
                    continue
        
            speed = activity.get('average_speed', None)
            score = strava_points(activity_type, duration_minutes, speed)


            _, created = Cardio.objects.get_or_create(
                user=user,
                external_id=f"Strava_{strava_activity_id}",
                defaults={
                    'activity': f"Strava: {activity_type}",
                    'date': activity_date,
                    'duration': duration_minutes,
                    'score': score,
                }
            )
            
            if created:
                workouts_added += 1
                user.profile.score += score
                user.profile.save()
                print(f"User {user} logged {score} points")
        else:
            #Sport Workout
            existing_workout = Sport.objects.filter(
                user=user,
                date=start_datetime
                ).first()

            if existing_workout:
                #if new workout is longer, update existing workout
                if duration_minutes > existing_workout.duration:
                    existing_workout.duration = duration_minutes
                    existing_workout.sport = f"Strava: {activity_type}"
                    existing_workout.external_id = f"Strava_{strava_activity_id}"
                    existing_workout.save()
                    print(f"Updated workout to longer duration: {duration_minutes} min")
                    continue
                else:
                    print(f"Skipping shorter duplicate workout")
                    continue
        
            score = strava_points(activity_type, duration_minutes, None)


            _, created = Sport.objects.get_or_create(
                user=user,
                external_id=f"Strava_{strava_activity_id}",
                defaults={
                    'sport': f"Strava: {activity_type}",
                    'date': activity_date,
                    'duration': duration_minutes,
                    'score': score,
                    'level': "recreational",
                }
            )
            
            if created:
                workouts_added += 1
                user.profile.score += score
                user.profile.save()
                print(f"User {user} logged {score} points")

    connection.last_sync = timezone.now()
    connection.save(update_fields=['last_sync', 'updated_at'])

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


##### Whoop sport ID -> human-readable name mapping

WHOOP_SPORT_IDS = {
    -1: "Activity",
    0: "Running",
    1: "Cycling",
    16: "Baseball",
    17: "Basketball",
    18: "Rowing",
    19: "Fencing",
    20: "Field Hockey",
    21: "Football",
    22: "Golf",
    24: "Ice Hockey",
    25: "Lacrosse",
    27: "Rugby",
    28: "Sailing",
    29: "Skiing",
    30: "Soccer",
    31: "Softball",
    32: "Squash",
    33: "Swimming",
    34: "Tennis",
    35: "Track & Field",
    36: "Volleyball",
    37: "Water Polo",
    38: "Wrestling",
    39: "Boxing",
    42: "Dance",
    43: "Pilates",
    44: "Yoga",
    45: "Weightlifting",
    47: "Cross Country Skiing",
    48: "Functional Fitness",
    49: "Duathlon",
    51: "Gymnastics",
    52: "Hiking/Rucking",
    53: "Horseback Riding",
    55: "Kayaking",
    56: "Martial Arts",
    57: "Mountain Biking",
    59: "Powerlifting",
    60: "Rock Climbing",
    61: "Paddleboarding",
    62: "Triathlon",
    63: "Walking",
    64: "Surfing",
    65: "Elliptical",
    66: "Stairmaster",
    70: "Meditation",
    71: "Other",
    73: "Diving",
    74: "Operations - Tactical",
    75: "Operations - Medical",
    76: "Operations - Flying",
    77: "Operations - Water",
    82: "Ultimate",
    83: "Climber",
    84: "Jumping Rope",
    85: "Australian Football",
    86: "Skateboarding",
    87: "Coaching",
    88: "Ice Bath",
    89: "Commuting",
    90: "Gaming",
    91: "Snowboarding",
    92: "Motocross",
    93: "Caddying",
    94: "Obstacle Course Racing",
    95: "Motor Racing",
    96: "HIIT",
    97: "Spin",
    98: "Jiu Jitsu",
    99: "Manual Labor",
    100: "Cricket",
    101: "Pickleball",
    102: "Inline Skating",
    103: "Box Fitness",
    104: "Spikeball",
    105: "Wheelchair Pushing",
    106: "Paddle Tennis",
    107: "Barre",
    108: "Stage Performance",
    109: "High Stress Work",
    110: "Parkour",
    111: "Gaelic Football",
    112: "Hurling/Camogie",
    113: "Circus Arts",
    121: "Massage Therapy",
    123: "Strength Trainer",
    125: "Watching Sports",
    126: "Assault Bike",
    127: "Kickboxing",
    128: "Stretching",
    230: "Table Tennis",
    231: "Badminton",
    232: "Netball",
    233: "Sauna",
    234: "Disc Golf",
    235: "Yard Work",
    236: "Air Compression",
    237: "Percussive Massage",
    238: "Paintball",
    239: "Ice Skating",
    240: "Handball",
    248: "F45 Training",
    249: "Padel",
    250: "Barry's",
    251: "Dedicated Parenting",
    252: "Stroller Walking",
    253: "Stroller Jogging",
    254: "Toddlerwearing",
    255: "Babywearing",
    258: "Barre3",
    259: "Hot Yoga",
    261: "Stadium Steps",
    262: "Polo",
    263: "Musical Performance",
    264: "Kite Boarding",
    266: "Dog Walking",
    267: "Water Skiing",
    268: "Wakeboarding",
    269: "Cooking",
    270: "Cleaning",
    272: "Public Speaking",
}


##### validate whoop signature (required for webhooks)

def validate_whoop_signature(request):
    signature = request.headers.get('X-WHOOP-Signature')
    timestamp = request.headers.get('X-WHOOP-Signature-Timestamp')

    if not signature or not timestamp:
        return False

    if not WHOOP_CLIENT_SECRET:
        logger.error("WHOOP_CLIENT_SECRET not configured")
        return False

    message = timestamp + request.body.decode('utf-8')
    expected = base64.b64encode(
        hmac.new(
            WHOOP_CLIENT_SECRET.encode(),
            message.encode(),
            hashlib.sha256
        ).digest()
    ).decode()

    return hmac.compare_digest(signature, expected)



####Automatic Add Workouts ###
def sync_whoop_for_user(user, days_back=30):
    try:
        connection = WearableConnection.objects.get(
            user=user,
            device_type='whoop',
            is_active=True
        )
    except WearableConnection.DoesNotExist:
        raise Exception("Whoop not connected")

    # REFRESH TOKEN IF EXPIRED (shared, lock-protected, tolerant of transient errors).
    # Whoop's docs include scope=offline on the refresh call so a new refresh token is issued.
    connection = ensure_fresh_token(
        connection,
        token_url='https://api.prod.whoop.com/oauth/oauth2/token',
        client_id=WHOOP_CLIENT_ID,
        client_secret=WHOOP_CLIENT_SECRET,
        provider_label='Whoop',
        extra_data={'scope': 'offline'},
    )

    end_time = timezone.now()
    start_time = end_time - timedelta(days=days_back)
    end_date = end_time.strftime('%Y-%m-%dT%H:%M:%S.000Z')
    start_date = start_time.strftime('%Y-%m-%dT%H:%M:%S.000Z')

    headers = {'Authorization': f'Bearer {connection.access_token}'}
    response = requests.get(
        f'https://api.prod.whoop.com/developer/v2/activity/workout?start={start_date}&end={end_date}',
        headers=headers,
        timeout=REQUEST_TIMEOUT,
    )

    if response.status_code == 401:
        # Whoop invalidates access tokens when the refresh token is used elsewhere; force
        # a refresh on the next sync instead of trusting expires_at.
        mark_token_expired(connection)
        raise Exception("Failed to fetch Whoop data: 401 (token rejected — will refresh on next sync)")
    if response.status_code != 200:
        print(f"Failed to fetch Whoop workouts: {response.status_code} - {response.text}")
        raise Exception(f"Failed to fetch Whoop data: {response.status_code}")

    data = response.json()
    workouts_added = 0

    for workout in data.get('records', []):
        # sport_id is deprecated in v2, so the map only covers payloads that still send
        # it; sport_name is the required field and carries the same display names.
        sport_id = workout.get('sport_id')
        sport_name = (
            WHOOP_SPORT_IDS.get(sport_id)
            or workout.get('sport_name')
            or f"Sport {sport_id}"
        )
        # CARDIO/EXCLUDED_ACTIVITIES/INTENSITY_INCLUDED hold squashed single tokens, so
        # drop everything but alphanumerics: "Hiking/Rucking" -> "hikingrucking"
        activity_lower = ''.join(c for c in sport_name.lower() if c.isalnum())

        whoop_workout_id = str(workout.get('id'))
        workout_start = workout.get('start')
        workout_end = workout.get('end')

        if workout_start and workout_end:
            start_dt = parse_datetime(workout_start)
            end_dt = parse_datetime(workout_end)
            duration_minutes = max(int((end_dt - start_dt).total_seconds()) // 60, 1)
        else:
            continue

        if duration_minutes < 10:
            continue

        # Walking/hiking minimum 45 min
        if activity_lower in EXCLUDED_ACTIVITIES and duration_minutes < 45:
            print(f"Skipping short Whoop {sport_name} ({duration_minutes} min)")
            continue

        workout_score = workout.get('score') or {}
        intensity = workout_score.get('strain') or 0
        score = whoop_points(activity_lower, duration_minutes, intensity)
        existing_cardio = Cardio.objects.filter(user=user, date=workout_start).first()
        existing_sport = Sport.objects.filter(user=user, date=workout_start).first()

        if activity_lower in CARDIO or activity_lower in EXCLUDED_ACTIVITIES:
            # Duplicate prevention: same user + same start time
           
            if existing_cardio:
                if duration_minutes > existing_cardio.duration:
                    existing_cardio.duration = duration_minutes
                    existing_cardio.activity = f"Whoop: {sport_name}"
                    existing_cardio.external_id = f"whoop_{whoop_workout_id}"
                    existing_cardio.save()
                    print(f"Updated Whoop Cardio Workout to longer duration: {duration_minutes} min")
                else:
                    print("Skipping shorter duplicate Whoop Cardio Workout")
                continue
            
            #duplicate sport workout already exists (Whoop bug)
            elif existing_sport:
                print("Skipping duplicate Cardio workout, Sport already exists")
                continue

            _, created = Cardio.objects.get_or_create(
                user=user,
                external_id=f"whoop_{whoop_workout_id}",
                defaults={
                    'activity': f"Whoop: {sport_name}",
                    'date': workout_start,
                    'duration': duration_minutes,
                    'score': score,
                }
            )
            if created:
                workouts_added += 1
                user.profile.score += score
                user.profile.save()
                print(f"User {user} logged {score} Whoop points.")

        else:
            #duplicate sport exists
            if existing_sport:
                if duration_minutes > existing_sport.duration:
                    existing_sport.duration = duration_minutes
                    existing_sport.sport = f"Whoop: {sport_name}"
                    existing_sport.external_id = f"whoop_{whoop_workout_id}"
                    existing_sport.save()
                    print(f"Updated Whoop sport workout to longer duration: {duration_minutes} min")
                else:
                    print("Skipping shorter duplicate Whoop sport workout")
                continue

            #Duplicate cardio already exists
            elif existing_cardio:
                print("Skipping duplicate Sport workout, Cardio already exists")
                continue

            _, created = Sport.objects.get_or_create(
                user=user,
                external_id=f"whoop_{whoop_workout_id}",
                defaults={
                    'sport': f"Whoop: {sport_name}",
                    'date': workout_start,
                    'duration': duration_minutes,
                    'score': score,
                    'level': "recreational",
                }
            )
            if created:
                workouts_added += 1
                user.profile.score += score
                user.profile.save()
                print(f"User {user} logged {score} Whoop points.")

    connection.last_sync = timezone.now()
    connection.save(update_fields=['last_sync', 'updated_at'])

    print(f"Whoop sync complete: {workouts_added} workouts added")
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
        f"scope=read:profile%20read:workout%20offline&"
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
        logger.info(f"Whoop token data keys: {list(token_data.keys())}")

        # Whoop includes user_id in the token response, but fall back to the
        # profile endpoint if it's missing so the webhook lookup never breaks.
        whoop_user_id = str(token_data.get('user_id', ''))
        if not whoop_user_id:
            try:
                profile_resp = requests.get(
                    'https://api.prod.whoop.com/developer/v2/user/profile/basic',
                    headers={'Authorization': f'Bearer {token_data["access_token"]}'}
                )
                if profile_resp.status_code == 200:
                    whoop_user_id = str(profile_resp.json().get('user_id', ''))
                    logger.info(f"Fetched Whoop user_id from profile: {whoop_user_id}")
                else:
                    logger.warning(f"Could not fetch Whoop profile: {profile_resp.status_code}")
            except Exception as profile_err:
                logger.warning(f"Profile fetch failed: {profile_err}")

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
                'refresh_failures': 0,
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
    
    if not validate_whoop_signature(request):
        return JsonResponse({'error': 'Invalid Signature'}, status=401)

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
                logger.error(f"No active connection for Whoop user {whoop_user_id}")
                # Return 200 so Whoop stops retrying — a 404/5xx causes Whoop
                # to keep sending the same webhook event indefinitely.
                return JsonResponse({'status': 'ignored', 'reason': 'no active connection'}, status=200)
            
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



###Check Connection Status Functino 

@csrf_exempt
def check_connection_status(request):
    """Check which wearables the user has connected"""
    user = get_user_from_token(request)
    
    if not user:
        return JsonResponse({'error': 'Authentication Required'}, status=401)
    
    connections = WearableConnection.objects.filter(user=user)

    status = {
        'oura': False,
        'strava': False,
        'whoop': False,
        'connections': [],
        # Providers whose connection was switched off because the provider kept rejecting
        # our refresh token; the user has to go through the connect flow again. (Explicit
        # disconnects delete the row, so anything inactive here is a broken link.)
        'needs_reconnect': [],
    }

    for connection in connections:
        if not connection.is_active:
            status['needs_reconnect'].append(connection.device_type)
            continue
        status[connection.device_type] = True
        status['connections'].append({
            'provider': connection.device_type,
            'connected_at': connection.created_at,
            'last_sync': connection.last_sync
        })

    return JsonResponse(status)



@csrf_exempt
def debug_sync(request):
    import traceback
    user = get_user_from_token(request)
    if not user:
        return JsonResponse({'error': 'Auth required'}, status=401)
    try:
        result = sync_oura_for_user(user, days_back=7)
        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({'error': str(e), 'trace': traceback.format_exc()})