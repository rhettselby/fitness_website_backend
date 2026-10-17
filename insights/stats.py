"""The numbers behind a weekly insight, worked out here so the model only has to phrase them.

Language models are unreliable at arithmetic, so every comparison an insight might lean on
("twice your usual", "a rest week after six active ones", "3rd on the leaderboard") is
computed in Python and handed over ready-made. Weeks are leaderboard weeks: Monday to
Sunday, Pacific time.
"""

from collections import defaultdict
from datetime import datetime, time, timedelta

from fitness.models import Booze, Cardio, Gym, Sport
from groups.models import WeeklyScore
from groups.tasks import PACIFIC

# The "usual week" is the average of up to this many weeks before the insight's week.
BASELINE_WEEKS = 8
# Activities listed per insight: enough for "you ran twice as much", few enough to stay focused.
MAX_ACTIVITIES = 6


def pacific_midnight(day):
    return datetime.combine(day, time.min, tzinfo=PACIFIC)


def _logs(user, before):
    """Every log the user made before ``before``, flattened to one shape with its Pacific day and week."""
    mine = {"user": user, "date__lt": before}
    logs = []
    for row in Cardio.objects.filter(**mine).values("date", "activity", "duration", "score"):
        logs.append({"kind": "cardio", "date": row["date"], "activity": row["activity"],
                     "minutes": row["duration"] or 0, "points": row["score"]})
    for row in Gym.objects.filter(**mine).values("date", "activity", "score"):
        logs.append({"kind": "gym", "date": row["date"], "activity": row["activity"],
                     "minutes": 0, "points": row["score"]})
    for row in Sport.objects.filter(**mine).values("date", "sport", "duration", "score"):
        logs.append({"kind": "sport", "date": row["date"], "activity": row["sport"],
                     "minutes": row["duration"] or 0, "points": row["score"]})
    for row in Booze.objects.filter(**mine).values("date", "number_of_drinks", "score"):
        logs.append({"kind": "booze", "date": row["date"], "drinks": row["number_of_drinks"] or 0,
                     "points": row["score"]})

    for log in logs:
        log["day"] = log["date"].astimezone(PACIFIC).date()
        log["week"] = log["day"] - timedelta(days=log["day"].weekday())
    return logs


def _totals(logs):
    # Booze is logged like a workout (it even scores points) but isn't exercise.
    workouts = [log for log in logs if log["kind"] != "booze"]
    return {
        "workouts": len(workouts),
        "cardio_sessions": sum(1 for log in workouts if log["kind"] == "cardio"),
        "gym_sessions": sum(1 for log in workouts if log["kind"] == "gym"),
        "sport_sessions": sum(1 for log in workouts if log["kind"] == "sport"),
        "active_minutes": sum(log["minutes"] for log in workouts),
        "active_days": len({log["day"] for log in workouts}),
        "drinks": sum(log["drinks"] for log in logs if log["kind"] == "booze"),
        "points": sum(log["points"] for log in logs),
    }


def _activities(this_week, baseline, baseline_weeks, earlier_keys):
    """Per-activity sessions and minutes this week next to the usual week, busiest first."""
    by_key = defaultdict(lambda: {"sessions": 0, "minutes": 0, "usual_sessions": 0, "usual_minutes": 0})
    names, kinds = {}, {}
    for logs, prefix in ((this_week, ""), (baseline, "usual_")):
        for log in logs:
            if log["kind"] == "booze":
                continue
            # Activity names are typed in by hand, so "running" and "Running " are the same thing.
            key = log["activity"].strip().lower()
            names.setdefault(key, log["activity"].strip())
            kinds.setdefault(key, log["kind"])
            by_key[key][prefix + "sessions"] += 1
            by_key[key][prefix + "minutes"] += log["minutes"]

    rows = []
    for key, counts in by_key.items():
        row = {"name": names[key], "type": kinds[key], "sessions": counts["sessions"]}
        if kinds[key] != "gym":  # gym sessions have no duration
            row["minutes"] = round(counts["minutes"])
        if baseline_weeks:
            row["usual_sessions"] = round(counts["usual_sessions"] / baseline_weeks, 1)
            if kinds[key] != "gym":
                row["usual_minutes"] = round(counts["usual_minutes"] / baseline_weeks, 1)
            # Never logged before this week, not merely absent from the baseline window.
            row["new"] = counts["sessions"] > 0 and key not in earlier_keys
        rows.append(row)

    rows.sort(key=lambda r: (r["sessions"], r.get("usual_sessions", 0)), reverse=True)
    return rows[:MAX_ACTIVITIES]


def _leaderboard(user, week_start):
    # Archived by groups.tasks.reset_scores, which runs before insights are generated.
    week = WeeklyScore.objects.filter(week_start=week_start)
    mine = week.filter(user=user).values_list("score", flat=True).first()
    if mine is None:
        return None
    return {"rank": week.filter(score__gt=mine).count() + 1, "of": week.count(), "score": mine}


def weekly_stats(user, week_start):
    """Everything the model is shown about ``user``'s week starting on Monday ``week_start``.

    ``usual_week`` is ``None`` for a user whose first ever log falls in this week. Otherwise
    it averages the up-to-``BASELINE_WEEKS`` weeks before this one, counting only weeks on or
    after the user's first log, so a new member isn't compared against weeks before they joined.
    """
    week_end = week_start + timedelta(days=7)
    logs = _logs(user, before=pacific_midnight(week_end))
    first_week = min((log["week"] for log in logs), default=week_start)
    baseline_start = max(week_start - timedelta(weeks=BASELINE_WEEKS), first_week)
    baseline_weeks = max((week_start - baseline_start).days // 7, 0)

    this_week = [log for log in logs if log["week"] == week_start]
    baseline = [log for log in logs if baseline_start <= log["week"] < week_start]
    earlier_keys = {log["activity"].strip().lower() for log in logs
                    if log["week"] < week_start and log["kind"] != "booze"}

    totals = _totals(this_week)
    stats = {
        "week": {"start": week_start.isoformat(), "end": (week_end - timedelta(days=1)).isoformat()},
        "this_week": {**totals, "active_minutes": round(totals["active_minutes"])},
        "usual_week": None,
        "activities": _activities(this_week, baseline, baseline_weeks, earlier_keys),
        "leaderboard": _leaderboard(user, week_start),
    }

    if baseline_weeks:
        usual = {key: value / baseline_weeks for key, value in _totals(baseline).items()}
        stats["usual_week"] = {key: round(value, 1) for key, value in usual.items()}
        stats["usual_week_is_average_of_weeks"] = baseline_weeks
        # this week / usual week, so 2.0 means twice as much. Left out where the usual is 0.
        stats["compared_to_usual"] = {
            key: round(totals[key] / value, 2) for key, value in usual.items() if value
        }

    active_weeks = {log["week"] for log in logs if log["kind"] != "booze"}

    def active_run_ending(week):
        run = 0
        while week - timedelta(weeks=run) in active_weeks:
            run += 1
        return run

    stats["active_week_streak"] = active_run_ending(week_start)
    if not stats["active_week_streak"]:
        stats["rest_week_after_active_weeks"] = active_run_ending(week_start - timedelta(weeks=1))
    return stats
