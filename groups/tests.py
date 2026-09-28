"""Tests for leaderboard history.

``reset_scores`` archives each user's weekly score as a ``WeeklyScore`` row before zeroing
``Profile.score``; the history endpoints read those rows back per group and globally.
"""

from datetime import date, datetime, timezone as dt_timezone
from unittest import mock

from django.contrib.auth.models import User
from django.db.models import F
from django.test import TestCase
from rest_framework_simplejwt.tokens import AccessToken

from profile_page.models import Profile
from .models import FitnessGroup, WeeklyScore
from .tasks import previous_monday_pacific, reset_scores


def make_user(username, score=0):
    user = User.objects.create_user(username=username, password="pw")
    Profile.objects.create(user=user, score=score)
    return user


class PreviousMondayPacificTests(TestCase):
    def test_monday_morning_run_labels_the_week_that_just_ended(self):
        # Beat fires 08:00 UTC Monday 2026-09-28 (01:00 PDT); the closed week began Monday the 21st.
        run_at = datetime(2026, 9, 28, 8, 0, tzinfo=dt_timezone.utc)
        self.assertEqual(previous_monday_pacific(run_at), date(2026, 9, 21))

    def test_late_monday_run_labels_the_same_week(self):
        run_at = datetime(2026, 9, 28, 23, 30, tzinfo=dt_timezone.utc)  # 16:30 PDT Monday
        self.assertEqual(previous_monday_pacific(run_at), date(2026, 9, 21))

    def test_uses_the_pacific_date_not_utc(self):
        # 02:00 UTC Tuesday is still 18:00 Monday in Los Angeles, so the week that just
        # ended is still the one that began the previous Monday.
        run_at = datetime(2026, 1, 13, 2, 0, tzinfo=dt_timezone.utc)
        self.assertEqual(previous_monday_pacific(run_at), date(2026, 1, 5))


class ResetScoresTests(TestCase):
    def setUp(self):
        self.alice = make_user("alice", score=500)
        self.bob = make_user("bob", score=300)
        make_user("idle", score=0)

    def scores(self):
        return {p.user.username: p.score for p in Profile.objects.select_related("user")}

    def test_archives_scores_then_resets_them(self):
        reset_scores()

        archived = {row.user.username: row.score for row in WeeklyScore.objects.select_related("user")}
        self.assertEqual(archived, {"alice": 500, "bob": 300})  # a zero score gets no row
        self.assertEqual(self.scores(), {"alice": 0, "bob": 0, "idle": 0})
        self.assertEqual(
            set(WeeklyScore.objects.values_list("week_start", flat=True)),
            {previous_monday_pacific()},
        )

    def test_second_run_is_a_noop(self):
        reset_scores()
        Profile.objects.filter(user=self.alice).update(score=120)  # points earned in the new week

        reset_scores()

        self.assertEqual(WeeklyScore.objects.count(), 2)
        self.assertEqual(WeeklyScore.objects.get(user=self.alice).score, 500)
        self.assertEqual(self.scores()["alice"], 120)

    def test_points_landing_mid_task_are_kept_for_the_new_week(self):
        real_bulk_create = WeeklyScore.objects.bulk_create

        def bulk_create_while_alice_logs_a_workout(objs, **kwargs):
            # A web request or wearable sync commits between the read and the reset.
            Profile.objects.filter(user=self.alice).update(score=F("score") + 40)
            return real_bulk_create(objs, **kwargs)

        with mock.patch.object(WeeklyScore.objects, "bulk_create",
                               side_effect=bulk_create_while_alice_logs_a_workout):
            reset_scores()

        self.assertEqual(WeeklyScore.objects.get(user=self.alice).score, 500)
        self.assertEqual(self.scores()["alice"], 40)


class LeaderboardHistoryApiTests(TestCase):
    def setUp(self):
        self.alice = make_user("alice")
        self.bob = make_user("bob")
        self.carol = make_user("carol")  # not in the group
        self.group = FitnessGroup.objects.create(name="Runners", owner=self.alice)
        self.group.members.add(self.alice, self.bob)

        WeeklyScore.objects.bulk_create([
            WeeklyScore(user=self.alice, week_start=date(2026, 9, 14), score=500),
            WeeklyScore(user=self.bob, week_start=date(2026, 9, 14), score=300),
            WeeklyScore(user=self.carol, week_start=date(2026, 9, 14), score=900),
            WeeklyScore(user=self.alice, week_start=date(2026, 9, 21), score=200),
            WeeklyScore(user=self.bob, week_start=date(2026, 9, 21), score=200),
            # A week only a non-member played: shows globally, not in the group's history.
            WeeklyScore(user=self.carol, week_start=date(2026, 9, 7), score=100),
        ])

    def get(self, url, user=None):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {AccessToken.for_user(user)}"} if user else {}
        return self.client.get(url, **headers)

    def group_url(self, week_start=""):
        return f"/groups/leaderboard_history/{self.group.id}/" + (f"{week_start}/" if week_start else "")

    def test_group_weeks_are_the_members_weeks_newest_first(self):
        res = self.get(self.group_url(), self.alice)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"weeks": ["2026-09-21", "2026-09-14"]})

    def test_group_week_ranks_current_members_only(self):
        res = self.get(self.group_url("2026-09-14"), self.bob)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {
            "week_start": "2026-09-14",
            "week_end": "2026-09-20",
            "leaderboard": [
                {"rank": 1, "user": "alice", "score": 500},
                {"rank": 2, "user": "bob", "score": 300},
            ],
        })
        self.assertIn("max-age=86400", res["Cache-Control"])  # past weeks never change

    def test_tied_scores_share_a_rank(self):
        res = self.get(self.group_url("2026-09-21"), self.alice)
        self.assertEqual(
            [(row["rank"], row["user"]) for row in res.json()["leaderboard"]],
            [(1, "alice"), (1, "bob")],
        )

    def test_non_member_is_forbidden(self):
        self.assertEqual(self.get(self.group_url(), self.carol).status_code, 403)
        self.assertEqual(self.get(self.group_url("2026-09-14"), self.carol).status_code, 403)

    def test_missing_token_and_unknown_group(self):
        self.assertEqual(self.get(self.group_url()).status_code, 401)
        self.assertEqual(self.get("/groups/leaderboard_history/999/", self.alice).status_code, 404)

    def test_malformed_week_start_is_a_400(self):
        self.assertEqual(self.get(self.group_url("last-week"), self.alice).status_code, 400)
        self.assertEqual(self.get("/api/leaderboard/history/last-week/", self.alice).status_code, 400)

    def test_global_weeks_and_week(self):
        res = self.get("/api/leaderboard/history/", self.carol)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"weeks": ["2026-09-21", "2026-09-14", "2026-09-07"]})

        res = self.get("/api/leaderboard/history/2026-09-14/", self.carol)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["week_end"], "2026-09-20")
        self.assertEqual(
            [(row["rank"], row["user"], row["score"]) for row in res.json()["leaderboard"]],
            [(1, "carol", 900), (2, "alice", 500), (3, "bob", 300)],
        )

    def test_global_week_is_capped_at_ten(self):
        for i in range(12):
            WeeklyScore.objects.create(user=make_user(f"u{i}"), week_start=date(2026, 9, 21), score=1000 + i)

        res = self.get("/api/leaderboard/history/2026-09-21/", self.alice)
        self.assertEqual(len(res.json()["leaderboard"]), 10)
        self.assertEqual(res.json()["leaderboard"][0], {"rank": 1, "user": "u11", "score": 1011})

    def test_global_history_requires_a_token(self):
        self.assertEqual(self.get("/api/leaderboard/history/").status_code, 401)
        self.assertEqual(self.get("/api/leaderboard/history/2026-09-14/").status_code, 401)
