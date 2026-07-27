from datetime import timedelta

from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.core import mail, signing
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from fitness.models import Cardio, Comment
from profile_page.models import Profile
from .emails import UNSUBSCRIBE_SALT
from .tasks import send_comment_notification, send_monthly_reminder_email


class CommentNotificationTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="owner", password="pw", email="owner@example.com")
        self.owner_profile = Profile.objects.create(user=self.owner)

        self.commenter = User.objects.create_user(
            username="commenter", password="pw", email="commenter@example.com")
        Profile.objects.create(user=self.commenter)

        self.workout = Cardio.objects.create(user=self.owner, activity="run", duration=30)

    def _add_comment(self, user, text="Nice work!"):
        return Comment.objects.create(
            user=user,
            text=text,
            content_type=ContentType.objects.get_for_model(self.workout),
            object_id=self.workout.id,
        )

    def test_sends_email_to_owner(self):
        comment = self._add_comment(self.commenter)
        send_comment_notification(comment.id)

        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.to, ["owner@example.com"])
        self.assertIn("commenter", msg.subject)
        self.owner_profile.refresh_from_db()
        self.assertIsNotNone(self.owner_profile.last_comment_email_at)

    def test_self_comment_does_not_email(self):
        comment = self._add_comment(self.owner)  # owner comments on own workout
        send_comment_notification(comment.id)
        self.assertEqual(len(mail.outbox), 0)

    def test_weekly_throttle_skips_second_email(self):
        send_comment_notification(self._add_comment(self.commenter).id)
        self.assertEqual(len(mail.outbox), 1)
        # A second comment within the cooldown window must not send again.
        send_comment_notification(self._add_comment(self.commenter, "Again!").id)
        self.assertEqual(len(mail.outbox), 1)

    def test_email_sent_again_after_cooldown(self):
        send_comment_notification(self._add_comment(self.commenter).id)
        self.assertEqual(len(mail.outbox), 1)
        # Pretend the last email was 8 days ago.
        self.owner_profile.last_comment_email_at = timezone.now() - timedelta(days=8)
        self.owner_profile.save(update_fields=["last_comment_email_at"])
        send_comment_notification(self._add_comment(self.commenter, "Later!").id)
        self.assertEqual(len(mail.outbox), 2)

    def test_unsubscribed_owner_does_not_email(self):
        self.owner_profile.email_notifications = False
        self.owner_profile.save(update_fields=["email_notifications"])
        send_comment_notification(self._add_comment(self.commenter).id)
        self.assertEqual(len(mail.outbox), 0)

    def test_owner_without_email_is_skipped(self):
        self.owner.email = ""
        self.owner.save(update_fields=["email"])
        send_comment_notification(self._add_comment(self.commenter).id)
        self.assertEqual(len(mail.outbox), 0)


class MonthlyReminderTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="member", password="pw", email="member@example.com")
        self.profile = Profile.objects.create(user=self.user)

    def test_sends_reminder(self):
        send_monthly_reminder_email(self.user.id)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["member@example.com"])

    def test_unsubscribed_user_skipped(self):
        self.profile.email_notifications = False
        self.profile.save(update_fields=["email_notifications"])
        send_monthly_reminder_email(self.user.id)
        self.assertEqual(len(mail.outbox), 0)


class UnsubscribeViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="member", password="pw", email="member@example.com")
        self.profile = Profile.objects.create(user=self.user)

    def test_valid_token_unsubscribes(self):
        token = signing.dumps(self.user.id, salt=UNSUBSCRIBE_SALT)
        resp = self.client.get(reverse("unsubscribe", args=[token]))
        self.assertEqual(resp.status_code, 200)
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.email_notifications)

    def test_tampered_token_rejected(self):
        resp = self.client.get(reverse("unsubscribe", args=["not-a-valid-token"]))
        self.assertEqual(resp.status_code, 400)
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.email_notifications)
