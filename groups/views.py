from datetime import date, timedelta
from django.http import JsonResponse
from django.utils.cache import patch_cache_control
from groups.models import FitnessGroup, WeeklyScore
from rest_framework_simplejwt.tokens import AccessToken
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.models import User




def get_user_from_token(request):
    auth_header = request.headers.get("Authorization", "")

    if not auth_header.startswith("Bearer "):
        return None

    token = auth_header.split(" ")[1]

    try:
        access_token = AccessToken(token)
        user_id = access_token["user_id"]
        #User is Django's built in User model
        return User.objects.get(id=user_id)
    
    except (InvalidToken, TokenError, User.DoesNotExist):
        return None




@csrf_exempt
@api_view(['GET'])
def view_groups(request):

    try:
        user = get_user_from_token(request)

        if not user:
            return JsonResponse({"error": "Authentication Required"}, status=401)
        
        groups = user.fitness_group.all()
        group_names = []

        for group in groups:
            group_names.append({"name": group.name, "id": group.id, "motto": group.motto})

        return JsonResponse({"groups": group_names})
    
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)




@csrf_exempt
@api_view(['GET'])
def get_leaderboard(request, group_id):
    try:
        user = get_user_from_token(request)

        if not user:
            return JsonResponse({"error": "Authentication Required", }, status=401)
        
        try:
            group = FitnessGroup.objects.get(id=group_id)

        except FitnessGroup.DoesNotExist:
            return JsonResponse({"error": "Group not found"}, status = 404)


        # select_related('profile') pulls each member's score in the same query
        members = group.members.all().select_related('profile')

        result = {}
        for member in members:
            score = member.profile.score
            result[member.id] = (score, member.username)
        
        #sort by score in descending order
        sorted_result = sorted(result.items(), key=lambda x: x[1][0], reverse=True)
        leaderboard = []
        for i in range(len(sorted_result)):
            user_id, info = sorted_result[i]
            score = info[0]
            username = info[1]
            leaderboard.append({"rank": i + 1, "user": username, "score": score,})

        return JsonResponse({"leaderboard": leaderboard})
    
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)




@csrf_exempt
@api_view(['POST'])
def join_group(request, group_id):
    try:
        user = get_user_from_token(request)
        if not user:
            return JsonResponse({"error": "Authentication Required"}, status=401)
        
        try:
            group = FitnessGroup.objects.get(id=group_id)
        except FitnessGroup.DoesNotExist:
            return JsonResponse({"error": "Group Not Found"}, status = 404)

        if user.fitness_group.filter(id=group_id).exists():
            return JsonResponse({"error": "User already in Group"}, status=400)
        #add group to user's ManyToManyField group
        group.members.add(user)

        return JsonResponse({"message": "User joined group"}, status=200)
    
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)





@csrf_exempt
@api_view(['POST'])
def create_group(request):
    try:
        user = get_user_from_token(request)
        if not user:
            return JsonResponse({"error": "Authentication Required"}, status=401)
        
        chosen_name = request.data.get("name")
        if not chosen_name:
            return JsonResponse({"error": "Name is required"}, status=400)
        
        motto = request.data.get("motto", None)
        
        group = FitnessGroup.objects.create(
            name = chosen_name,
            owner = user,
            motto = motto,
        )

        group.members.add(user)

        return JsonResponse({"message": "Group Created Successfully"}, status=201)

    except Exception as e:
        return JsonResponse({"error": str(e)}, status = 500)




##### Leaderboard history: past weeks archived by groups.tasks.reset_scores #####

def parse_week_start(value):
    """The <week_start> URL segment as a date, or None when it isn't YYYY-MM-DD."""
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def history_response(week_start, rows):
    """JSON for one archived week, in the same row shape as get_leaderboard.

    ``rows`` are WeeklyScore objects already ordered by -score. Equal scores share a rank
    and the next distinct score skips ahead (1, 1, 3).
    """
    leaderboard = []
    for i, row in enumerate(rows):
        tied = leaderboard and leaderboard[-1]["score"] == row.score
        leaderboard.append({
            "rank": leaderboard[-1]["rank"] if tied else i + 1,
            "user": row.user.username,
            "score": row.score,
        })

    response = JsonResponse({
        "week_start": week_start.isoformat(),
        "week_end": (week_start + timedelta(days=6)).isoformat(),
        "leaderboard": leaderboard,
    })
    # A past week never changes, so the browser can keep it for a day.
    patch_cache_control(response, max_age=86400, private=True)
    return response


def member_group_or_error(request, group_id):
    """The group a history request is about, or the JSON error to send instead.

    Returns (group, None) when the caller has a valid token and is a member, otherwise
    (None, response): 401 without a token, 404 for an unknown group, 403 for a non-member.
    """
    user = get_user_from_token(request)
    if not user:
        return None, JsonResponse({"error": "Authentication Required"}, status=401)

    try:
        group = FitnessGroup.objects.get(id=group_id)
    except FitnessGroup.DoesNotExist:
        return None, JsonResponse({"error": "Group not found"}, status=404)

    if not group.members.filter(id=user.id).exists():
        return None, JsonResponse({"error": "You are not a member of this group"}, status=403)

    return group, None


@csrf_exempt
@api_view(['GET'])
def leaderboard_history_weeks(request, group_id):
    """Weeks with archived scores for any current member, newest first."""
    try:
        group, error = member_group_or_error(request, group_id)
        if error:
            return error

        weeks = (
            WeeklyScore.objects
            .filter(user__in=group.members.all())
            .order_by("-week_start")
            .values_list("week_start", flat=True)
            .distinct()
        )
        return JsonResponse({"weeks": [week.isoformat() for week in weeks]})

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
@api_view(['GET'])
def leaderboard_history_week(request, group_id, week_start):
    """The group's leaderboard for one past week (current members only)."""
    try:
        group, error = member_group_or_error(request, group_id)
        if error:
            return error

        week = parse_week_start(week_start)
        if week is None:
            return JsonResponse({"error": "week_start must be YYYY-MM-DD"}, status=400)

        rows = (
            WeeklyScore.objects
            .filter(week_start=week, user__in=group.members.all())
            .select_related("user")
            .order_by("-score", "user__username")
        )
        return history_response(week, rows)

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
