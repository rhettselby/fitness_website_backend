"""Tests for AI weekly insights.

``insights.stats.weekly_stats`` works out a user's leaderboard week against their usual week,
``insights.tasks.generate_weekly_insight`` has Claude turn those numbers into a one-line card,
and the ``/api/insights/`` views hand the cards to the signed-in user.
"""

import json
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest import mock

import anthropic
import httpx
from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework_simplejwt.tokens import AccessToken

from fitness.models import Booze, Cardio, Gym
from groups.models import WeeklyScore
from groups.tasks import PACIFIC
from .models import WeeklyInsight
from .stats import weekly_stats
from .tasks import INSIGHT_MODEL, generate_weekly_insight, generate_weekly_insights

WEEK = date(2026, 10, 5)  # a Monday


def at(day, hour=12, minute=0):
    """``day`` at a Pacific wall-clock time, as stored on a workout."""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=PACIFIC)


def weeks_before(n, weekday=2):
    return WEEK - timedelta(weeks=n) + timedelta(days=weekday)


def run(user, day, minutes=30, hour=12, minute=0, activity="Running"):
    return Cardio.objects.create(user=user, activity=activity, duration=minutes, score=100 + minutes,
                                 date=at(day, hour, minute))


def claude_reply(stop_reason="end_turn", text=None):
    if text is None:
        text = json.dumps({
            "headline": "You ran twice as much this week as in a usual week",
            "detail": "Two runs, an hour in total, against your usual half hour.",
            "theme": "more_active",
        })
    # Opus 5.5 always thinks, so a real reply leads with a thinking block before the text.
    return SimpleNamespace(stop_reason=stop_reason, content=[
        SimpleNamespace(type="thinking", thinking=""),
        SimpleNamespace(type="text", text=text),
    ])


def api_error(cls, status):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("error", response=httpx.Response(status, request=request), body=None)


class WeeklyStatsTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pw")

    def test_compares_the_week_with_the_usual_week(self):
        for n in range(1, 5):  # one 30-minute run in each of the four weeks before
            run(self.alice, weeks_before(n))
        run(self.alice, WEEK)
        run(self.alice, WEEK + timedelta(days=2))
        Gym.objects.create(user=self.alice, activity="Push day", score=150, date=at(WEEK + timedelta(days=4)))

        stats = weekly_stats(self.alice, WEEK)

        self.assertEqual(stats["week"], {"start": "2026-10-05", "end": "2026-10-11"})
        self.assertEqual(stats["this_week"], {
            "workouts": 3, "cardio_sessions": 2, "gym_sessions": 1, "sport_sessions": 0,
            "active_minutes": 60, "active_days": 3, "drinks": 0, "points": 410,
        })
        self.assertEqual(stats["usual_week_is_average_of_weeks"], 4)
        self.assertEqual(stats["usual_week"]["workouts"], 1.0)
        self.assertEqual(stats["usual_week"]["active_minutes"], 30.0)
        self.assertEqual(stats["compared_to_usual"]["workouts"], 3.0)
        self.assertEqual(stats["compared_to_usual"]["active_minutes"], 2.0)
        self.assertNotIn("gym_sessions", stats["compared_to_usual"])  # no gym in a usual week
        self.assertEqual(stats["activities"], [
            {"name": "Running", "type": "cardio", "sessions": 2, "minutes": 60,
             "usual_sessions": 1.0, "usual_minutes": 30.0, "new": False},
            {"name": "Push day", "type": "gym", "sessions": 1, "usual_sessions": 0.0, "new": True},
        ])
        self.assertEqual(stats["active_week_streak"], 5)
        self.assertNotIn("rest_week_after_active_weeks", stats)

    def test_usual_week_only_counts_weeks_since_the_first_log(self):
        run(self.alice, weeks_before(2))
        run(self.alice, WEEK)

        stats = weekly_stats(self.alice, WEEK)

        # Two weeks of history (one run, then nothing), not eight weeks diluted by pre-signup zeros.
        self.assertEqual(stats["usual_week_is_average_of_weeks"], 2)
        self.assertEqual(stats["usual_week"]["workouts"], 0.5)
        self.assertEqual(stats["compared_to_usual"]["workouts"], 2.0)

    def test_usual_week_is_capped_and_new_means_never_logged(self):
        run(self.alice, weeks_before(20), activity="Climbing")
        run(self.alice, WEEK, activity="climbing ")

        stats = weekly_stats(self.alice, WEEK)

        self.assertEqual(stats["usual_week_is_average_of_weeks"], 8)
        self.assertEqual(stats["usual_week"]["workouts"], 0.0)
        self.assertNotIn("workouts", stats["compared_to_usual"])
        # Absent from the eight-week baseline, but climbed 20 weeks ago, so not "new".
        self.assertEqual(stats["activities"][0]["name"], "climbing")
        self.assertFalse(stats["activities"][0]["new"])

    def test_weeks_follow_pacific_time(self):
        run(self.alice, WEEK - timedelta(days=1), hour=23, minute=30)  # Sunday night: last week
        run(self.alice, WEEK, hour=0, minute=30)                      # Monday 00:30: this week (07:30 UTC)
        run(self.alice, WEEK + timedelta(days=6), hour=23, minute=30)  # Sunday 23:30: this week (Monday UTC)
        run(self.alice, WEEK + timedelta(days=7), hour=0, minute=30)   # next Monday: not counted at all

        stats = weekly_stats(self.alice, WEEK)

        self.assertEqual(stats["this_week"]["workouts"], 2)
        self.assertEqual(stats["usual_week"]["workouts"], 1.0)

    def test_first_week_has_no_usual_week(self):
        run(self.alice, WEEK)

        stats = weekly_stats(self.alice, WEEK)

        self.assertIsNone(stats["usual_week"])
        self.assertNotIn("compared_to_usual", stats)
        self.assertEqual(stats["activities"], [{"name": "Running", "type": "cardio", "sessions": 1, "minutes": 30}])
        self.assertEqual(stats["active_week_streak"], 1)

    def test_rest_week_reports_the_run_of_active_weeks_before_it(self):
        run(self.alice, weeks_before(5))  # then a gap week, then three active weeks
        for n in range(1, 4):
            run(self.alice, weeks_before(n))

        stats = weekly_stats(self.alice, WEEK)

        self.assertEqual(stats["this_week"]["workouts"], 0)
        self.assertEqual(stats["active_week_streak"], 0)
        self.assertEqual(stats["rest_week_after_active_weeks"], 3)

    def test_drinks_are_not_workouts(self):
        Booze.objects.create(user=self.alice, number_of_drinks=3, score=26, date=at(WEEK))

        stats = weekly_stats(self.alice, WEEK)

        self.assertEqual(stats["this_week"]["workouts"], 0)
        self.assertEqual(stats["this_week"]["drinks"], 3)
        self.assertEqual(stats["this_week"]["points"], 26)
        self.assertEqual(stats["activities"], [])

    def test_leaderboard_finish(self):
        bob = User.objects.create_user(username="bob", password="pw")
        carol = User.objects.create_user(username="carol", password="pw")
        WeeklyScore.objects.create(user=self.alice, week_start=WEEK, score=300)
        WeeklyScore.objects.create(user=bob, week_start=WEEK, score=500)
        WeeklyScore.objects.create(user=carol, week_start=WEEK, score=100)
        WeeklyScore.objects.create(user=carol, week_start=WEEK - timedelta(weeks=1), score=900)

        self.assertEqual(weekly_stats(self.alice, WEEK)["leaderboard"], {"rank": 2, "of": 3, "score": 300})
        self.assertIsNone(weekly_stats(bob, WEEK - timedelta(weeks=1))["leaderboard"])


class GenerateWeeklyInsightTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pw")
        run(self.alice, weeks_before(1))
        run(self.alice, WEEK)
        run(self.alice, WEEK + timedelta(days=2))
        patcher = mock.patch("insights.tasks.anthropic.Anthropic")
        self.create = patcher.start().return_value.beta.messages.create
        self.addCleanup(patcher.stop)

    def generate(self):
        return generate_weekly_insight(self.alice.id, WEEK.isoformat())

    def test_saves_the_card_with_the_stats_claude_saw(self):
        self.create.return_value = claude_reply()

        self.generate()

        insight = WeeklyInsight.objects.get(user=self.alice, week_start=WEEK)
        self.assertEqual(insight.headline, "You ran twice as much this week as in a usual week")
        self.assertEqual(insight.theme, "more_active")
        self.assertEqual(insight.stats, weekly_stats(self.alice, WEEK))

        request = self.create.call_args.kwargs
        self.assertEqual(request["model"], INSIGHT_MODEL)
        self.assertEqual(request["output_config"]["format"]["type"], "json_schema")
        self.assertEqual(request["output_config"]["effort"], "low")
        self.assertEqual(request["extra_body"], {"fallbacks": "default"})
        self.assertIn("server-side-fallback-2026-07-01", request["betas"])
        self.assertEqual(json.loads(request["messages"][0]["content"]), insight.stats)

    def test_existing_insight_skips_the_api(self):
        WeeklyInsight.objects.create(user=self.alice, week_start=WEEK, headline="Already here", theme="rest")

        self.generate()

        self.create.assert_not_called()
        self.assertEqual(WeeklyInsight.objects.get(user=self.alice).headline, "Already here")

    def test_refusal_saves_nothing(self):
        self.create.return_value = claude_reply(stop_reason="refusal", text="")

        self.generate()

        self.assertFalse(WeeklyInsight.objects.exists())

    def test_unparseable_output_saves_nothing(self):
        self.create.return_value = claude_reply(text="You ran a lot!")

        self.generate()

        self.assertFalse(WeeklyInsight.objects.exists())

    def test_rate_limit_is_retried(self):
        self.create.side_effect = api_error(anthropic.RateLimitError, 429)

        # Called directly rather than by a worker, Celery's retry() re-raises the original error.
        with self.assertRaises(anthropic.RateLimitError):
            self.generate()
        self.assertFalse(WeeklyInsight.objects.exists())

    def test_bad_request_is_dropped(self):
        self.create.side_effect = api_error(anthropic.BadRequestError, 400)

        self.generate()  # logged, not raised: retrying the same request would fail the same way

        self.assertFalse(WeeklyInsight.objects.exists())


class GenerateWeeklyInsightsTests(TestCase):
    def test_queues_recently_active_users_without_an_insight(self):
        this_week = User.objects.create_user(username="this_week", password="pw")
        resting = User.objects.create_user(username="resting", password="pw")
        lapsed = User.objects.create_user(username="lapsed", password="pw")
        done = User.objects.create_user(username="done", password="pw")
        deactivated = User.objects.create_user(username="deactivated", password="pw", is_active=False)
        Booze.objects.create(user=this_week, number_of_drinks=1, score=22, date=at(WEEK))
        run(resting, weeks_before(3))
        run(lapsed, weeks_before(4))
        run(done, WEEK)
        run(deactivated, WEEK)
        WeeklyInsight.objects.create(user=done, week_start=WEEK, headline="Done", theme="rest")

        with mock.patch("insights.tasks.generate_weekly_insight.delay") as delay:
            generate_weekly_insights(WEEK.isoformat())

        self.assertEqual(delay.call_args_list, [
            mock.call(this_week.id, "2026-10-05"),
            mock.call(resting.id, "2026-10-05"),
        ])


class InsightViewTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pw")
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {AccessToken.for_user(self.alice)}"}

    def add(self, user, week, headline):
        return WeeklyInsight.objects.create(user=user, week_start=week, headline=headline,
                                            detail="", theme="consistency", stats={"active_week_streak": 2})

    def test_requires_a_token(self):
        self.assertEqual(self.client.get("/api/insights/latest/").status_code, 401)
        self.assertEqual(self.client.get("/api/insights/").status_code, 401)

    def test_latest_is_null_before_the_first_insight(self):
        response = self.client.get("/api/insights/latest/", **self.auth)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"insight": None})

    def test_latest_returns_the_newest_of_the_users_own_insights(self):
        self.add(self.alice, WEEK - timedelta(weeks=1), "Older")
        self.add(self.alice, WEEK, "Newest")
        self.add(User.objects.create_user(username="bob", password="pw"), WEEK + timedelta(weeks=1), "Bob's")

        insight = self.client.get("/api/insights/latest/", **self.auth).json()["insight"]

        self.assertEqual(insight["headline"], "Newest")
        self.assertEqual(insight["week_start"], "2026-10-05")
        self.assertEqual(insight["week_end"], "2026-10-11")
        self.assertEqual(insight["stats"], {"active_week_streak": 2})

    def test_history_is_newest_first(self):
        self.add(self.alice, WEEK - timedelta(weeks=1), "Older")
        self.add(self.alice, WEEK, "Newest")

        insights = self.client.get("/api/insights/", **self.auth).json()["insights"]

        self.assertEqual([i["headline"] for i in insights], ["Newest", "Older"])
