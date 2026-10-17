from datetime import timedelta

from django.http import JsonResponse

from fitness.views import get_user_from_token
from .models import WeeklyInsight

HISTORY_LIMIT = 12


def serialize(insight):
    return {
        "id": insight.id,
        "week_start": insight.week_start.isoformat(),
        "week_end": (insight.week_start + timedelta(days=6)).isoformat(),
        "headline": insight.headline,
        "detail": insight.detail,
        "theme": insight.theme,
        "stats": insight.stats,
        "created_at": insight.created_at.isoformat(),
    }


def latest_insight_api_jwt(request):
    """The signed-in user's most recent weekly insight, or null before their first one."""
    if request.method != "GET":
        return JsonResponse({"error": "GET only"}, status=405)

    user = get_user_from_token(request)
    if not user:
        return JsonResponse({"error": "Authentication required"}, status=401)

    insight = WeeklyInsight.objects.filter(user=user).first()
    return JsonResponse({"insight": serialize(insight) if insight else None})


def insight_history_api_jwt(request):
    """The signed-in user's recent weekly insights, newest first."""
    if request.method != "GET":
        return JsonResponse({"error": "GET only"}, status=405)

    user = get_user_from_token(request)
    if not user:
        return JsonResponse({"error": "Authentication required"}, status=401)

    insights = WeeklyInsight.objects.filter(user=user)[:HISTORY_LIMIT]
    return JsonResponse({"insights": [serialize(insight) for insight in insights]})
