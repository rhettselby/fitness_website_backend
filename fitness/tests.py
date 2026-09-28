"""Tests for photo verification.

A workout photo queues ``groups.tasks.verify_workout_image``, which asks Claude whether the photo
shows the workout and, the first time it does, marks the workout verified and awards the bonus to
the workout and its owner's profile.
"""

import json
from types import SimpleNamespace
from unittest import mock

from cloudinary import CloudinaryResource
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework_simplejwt.tokens import AccessToken

from groups.tasks import VERIFICATION_BONUS, verify_workout_image
from profile_page.models import Profile
from .models import Booze, Cardio, Sport


def make_user(username, score=0):
    user = User.objects.create_user(username=username, password="pw")
    Profile.objects.create(user=user, score=score)
    return user


def claude_reply(verified=True, stop_reason="end_turn", text=None):
    if text is None:
        text = json.dumps({"verified": verified, "reasoning": "running shoes on a track"})
    return SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)])


class VerifyWorkoutImageTests(TestCase):
    def setUp(self):
        self.alice = make_user("alice", score=100)
        self.run = Cardio.objects.create(user=self.alice, activity="Running", score=100)
        patcher = mock.patch("groups.tasks.anthropic.Anthropic")
        self.create = patcher.start().return_value.messages.create
        self.addCleanup(patcher.stop)

    def verify(self, workout_type="cardio", workout_id=None):
        return verify_workout_image(
            "https://example.com/run.jpg", "Running", workout_type, workout_id or self.run.id, self.alice.id
        )

    def assertScores(self, workout, profile):
        self.run.refresh_from_db()
        self.assertEqual(self.run.score, workout)
        self.assertEqual(Profile.objects.get(user=self.alice).score, profile)

    def test_yes_verifies_and_awards_the_bonus(self):
        self.create.return_value = claude_reply(verified=True)

        self.assertTrue(self.verify())

        self.run.refresh_from_db()
        self.assertTrue(self.run.verified)
        self.assertScores(100 + VERIFICATION_BONUS, 100 + VERIFICATION_BONUS)
        self.assertEqual(self.create.call_args.kwargs["output_config"]["format"]["type"], "json_schema")

    def test_no_leaves_the_workout_unverified(self):
        self.create.return_value = claude_reply(verified=False)

        self.assertFalse(self.verify())

        self.run.refresh_from_db()
        self.assertFalse(self.run.verified)
        self.assertScores(100, 100)

    def test_bonus_is_awarded_once_per_workout(self):
        self.create.return_value = claude_reply(verified=True)

        self.verify()
        self.verify()  # e.g. the photo was replaced through add-image

        self.assertEqual(self.create.call_count, 1)  # the second run never reaches the API
        self.assertScores(100 + VERIFICATION_BONUS, 100 + VERIFICATION_BONUS)

    def test_racing_tasks_award_once(self):
        # Both tasks pass the "already verified?" check before either writes.
        def other_task_finishes_first(**kwargs):
            Cardio.objects.filter(id=self.run.id).update(verified=True)
            return claude_reply(verified=True)

        self.create.side_effect = other_task_finishes_first

        self.assertTrue(self.verify())
        self.assertScores(100, 100)

    def test_booze_and_unknown_types_skip_the_api(self):
        booze = Booze.objects.create(user=self.alice, number_of_drinks=2, drinks=["beer"])

        self.assertFalse(self.verify("booze", booze.id))
        self.assertFalse(self.verify("yoga"))
        self.create.assert_not_called()

    def test_reply_that_starts_with_markdown_still_counts(self):
        # The old startswith("yes") check read this as a "no".
        self.create.return_value = claude_reply(text='{"verified": true, "reasoning": "**Yes** - a track"}')

        self.assertTrue(self.verify())

    def test_incomplete_replies_do_not_verify(self):
        for reply in (
            claude_reply(stop_reason="refusal", text=""),
            claude_reply(stop_reason="max_tokens", text='{"verified": tr'),
            claude_reply(text="not json"),
        ):
            self.create.return_value = reply
            self.assertFalse(self.verify())
        self.assertScores(100, 100)


@mock.patch("fitness.views.verify_workout_image")
@mock.patch(
    "cloudinary.models.uploader.upload_resource",
    return_value=CloudinaryResource("workouts/abc", format="jpg", version=1, type="upload", resource_type="image"),
)
class WorkoutPhotoViewTests(TestCase):
    def setUp(self):
        self.alice = make_user("alice")
        self.bob = make_user("bob")

    def post(self, url, user, data):
        data = {**data, "image": SimpleUploadedFile("photo.jpg", b"jpg-bytes", content_type="image/jpeg")}
        return self.client.post(url, data, HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")

    def add_image(self, workout, workout_type, user):
        return self.post(f"/api/fitness/api/add-image/{workout.id}/", user, {"workout_type": workout_type})

    def test_add_image_to_sport_uses_the_sport_name(self, upload, verify):
        game = Sport.objects.create(user=self.alice, sport="Basketball")

        res = self.add_image(game, "sport", self.alice)

        self.assertEqual(res.status_code, 201)
        verify.delay.assert_called_once()
        _, exercise, workout_type, workout_id, user_id = verify.delay.call_args.args
        self.assertEqual((exercise, workout_type, workout_id, user_id), ("Basketball", "sport", game.id, self.alice.id))

    def test_add_image_to_someone_elses_workout_is_a_404(self, upload, verify):
        run = Cardio.objects.create(user=self.alice, activity="Running")

        res = self.add_image(run, "cardio", self.bob)

        self.assertEqual(res.status_code, 404)
        upload.assert_not_called()
        verify.delay.assert_not_called()
        run.refresh_from_db()
        self.assertFalse(run.image)

    def test_add_image_with_unknown_type_is_a_404(self, upload, verify):
        run = Cardio.objects.create(user=self.alice, activity="Running")

        self.assertEqual(self.add_image(run, "booze", self.alice).status_code, 404)
        verify.delay.assert_not_called()

    def test_booze_photo_is_not_sent_for_verification(self, upload, verify):
        res = self.post("/api/fitness/add/booze/", self.alice, {"number_of_drinks": 2, "drinks": '["beer"]'})

        self.assertEqual(res.status_code, 201)
        self.assertIsNotNone(res.json()["booze"]["image_url"])
        verify.delay.assert_not_called()
