from django.utils import timezone
import pytz
from datetime import timedelta
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.shortcuts import render
from django.contrib.auth import get_user_model

from fitness.models import Cardio, Gym, Sport
from rest_framework_simplejwt.tokens import AccessToken
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

User = get_user_model()

def get_user_from_token(request):
    auth_header = request.headers.get("Authorization", "")

    if not auth_header.startswith("Bearer "):
        return None

    token = auth_header.split(" ")[1]

    try:
        access_token = AccessToken(token)
        user_id = access_token["user_id"]
        return User.objects.get(id=user_id)
    except (InvalidToken, TokenError, User.DoesNotExist):
        return None

#JSON Is User Authenticated Check

def me_api(request):
    if request.user.is_authenticated:
        user = request.user
        data = {
            "isAuthenticated": True,
            "user": {
                "id": user.id,
                "username": user.username,
                "email": user.email,
            }
        }
    else:
        data = {"isAuthenticated": False}

    return JsonResponse(data)





def now_utc():
    # timezone-aware "now"
    return timezone.now()


def beginning_of_week(dt):
    # define start of the week as Monday 00:00 local time
    start_of_day = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    days_since_monday = start_of_day.weekday()  # Monday=0 ... Sunday=6
    week_start = start_of_day - timezone.timedelta(days=days_since_monday)
    return week_start



def about(request):
    return render(request, 'about.html')

def homepage(request):
    context = {}

    if request.user.is_authenticated:
        current_time = now_utc()
        week_start = beginning_of_week(current_time)

        # Use datetime for filtering since Workout.date is DateTimeField
        start_datetime = week_start
        end_datetime = current_time

        # Count this user's Cardio workouts this week
        cardio_count = (
            Cardio.objects
            .filter(user=request.user,
                    date__gte=start_datetime,
                    date__lte=end_datetime)
            .count()
        )

        # Count this user's Gym workouts this week
        gym_count = (
            Gym.objects
            .filter(user=request.user,
                    date__gte=start_datetime,
                    date__lte=end_datetime)
            .count()
        )

        user_weekly_count = cardio_count + gym_count

        context["user_weekly_count"] = user_weekly_count
        context["week_start"] = week_start.date()
        context["week_end"] = current_time.date()

    return render(request, 'home.html', context)

@login_required(login_url="/users/login/")
def leaderboard(request):
    
    pst = pytz.timezone('America/Los_Angeles')
    current_time = timezone.now().astimezone(pst)

    #Create one week zone
    today = timezone.now().astimezone(pst)
    start_of_week = today - timedelta(days=today.weekday())
    end_of_week = start_of_week + timedelta(days=7)



    # 1. Count Cardio workouts this week
    cardio_counts = (
        Cardio.objects
        .filter(date__gte=start_of_week, date__lte=end_of_week)
        .values("user")
        .annotate(count=Count("id"))
    )

    # 2. Count Gym workouts this week
    gym_counts = (
        Gym.objects
        .filter(date__gte=start_of_week, date__lte=end_of_week)
        .values("user")
        .annotate(count=Count("id"))
    )

    # 3. Merge counts per user
    totals = {}  # user_id -> total workouts this week

    for row in cardio_counts:
        uid = row["user"]
        totals[uid] = totals.get(uid, 0) + row["count"]

    for row in gym_counts:
        uid = row["user"]
        totals[uid] = totals.get(uid, 0) + row["count"]

    # 4. Build leaderboard rows
    User = get_user_model()
    leaderboard_rows = []
    for user_id, workout_count in totals.items():
        try:
            user_obj = User.objects.get(id=user_id)
        except User.DoesNotExist:
            continue

        leaderboard_rows.append({
            "user": user_obj,
            "count": workout_count,
        })

    # 5. Sort by most workouts
    leaderboard_rows.sort(key=lambda row: row["count"], reverse=True)

    # 6. How many workouts current user has
    current_user_count = 0
    for row in leaderboard_rows:
        if row["user"] == request.user:
            current_user_count = row["count"]
            break

    context = {
        "leaderboard": leaderboard_rows,
        "week_start": start_of_week.date(),
        "week_end": current_time.date(),
        "current_user_count": current_user_count,
    }

    return render(request, "leaderboard.html", context)

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from profile_page.models import Profile

@csrf_exempt
def leaderboard_api(request):
    # Handle CORS preflight requests
    if request.method == 'OPTIONS':
        response = JsonResponse({})
        response['Access-Control-Allow-Origin'] = 'http://localhost:5173'
        response['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
        response['Access-Control-Allow-Headers'] = 'Content-Type'
        response['Access-Control-Allow-Credentials'] = 'true'
        return response

    top_profiles = (
        Profile.objects
        .select_related('user')
        .order_by('-score')[:10]
    )

    data = [
        {
            'username': p.user.username,
            'score': p.score,
            'bio': p.bio or None,
            'location': p.location or None,
        }
        for p in top_profiles
    ]

    response = JsonResponse({"leaderboard": data})
    response['Access-Control-Allow-Origin'] = 'http://localhost:5173'
    response['Access-Control-Allow-Credentials'] = 'true'
    return response


### JWT Authentication Leaderboard Version

from django.utils import timezone
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")
@csrf_exempt
def leaderboard_api_jwt(request):
    if request.method != "GET":
        return JsonResponse({"error": "GET only"}, status=405)

    user = get_user_from_token(request)
    if not user:
        return JsonResponse({"error": "Authentication required"}, status=401)

    top_profiles = (
        Profile.objects
        .select_related('user')
        .order_by('-score')[:10]
    )

    data = [
        {
            'username': p.user.username,
            'score': p.score,
            'bio': p.bio or None,
            'location': p.location or None,
        }
        for p in top_profiles
    ]

    return JsonResponse({"leaderboard": data})