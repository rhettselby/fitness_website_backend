"""Tests for the wearable sync token-refresh path.

Background (Aug 2026): Whoop's token endpoint returned transient 502s around hourly
boundaries; the old refresh code deactivated a connection on ANY non-200, so every Whoop
user was silently disconnected. Oura had the mirror problem (never deactivating, retrying a
dead token forever). These tests pin down the intended behaviour of the shared
``ensure_fresh_token`` helper and the sync functions that use it.
"""

import json
from datetime import timedelta
from unittest import mock

import requests
from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from fitness.models import Cardio, Sport
from notifications.tasks import send_wearable_reconnect_email
from profile_page.models import Profile
from . import views
from .models import WearableConnection
from .views import (CONFIG_ERROR, REFRESH_FAILURE_LIMIT, TOKEN_REJECTED, TRANSIENT,
                    TokenRefreshError, add_workout, classify_refresh_failure, ensure_fresh_token,
                    sync_oura_for_user, sync_strava_for_user, sync_whoop_for_user)


class FakeResponse:
    """Minimal stand-in for requests.Response."""

    def __init__(self, status_code, payload=None, text=None):
        self.status_code = status_code
        self._payload = payload
        if text is not None:
            self.text = text
        else:
            self.text = json.dumps(payload) if payload is not None else ''

    def json(self):
        if self._payload is None:
            raise ValueError("no JSON body")
        return self._payload


def iso(dt):
    return dt.strftime('%Y-%m-%dT%H:%M:%S.000Z')


def whoop_payload(now=None, workout_id='w-1', sport_name='Running', minutes=45, strain=12.0):
    now = now or timezone.now()
    return {"records": [{
        "id": workout_id, "user_id": 22452524, "sport_id": 0, "sport_name": sport_name,
        "start": iso(now - timedelta(hours=3)), "end": iso(now - timedelta(hours=3) + timedelta(minutes=minutes)),
        "score_state": "SCORED", "score": {"strain": strain},
    }], "next_token": None}


REFRESH_OK = {"access_token": "A2", "refresh_token": "R2", "expires_in": 3600, "scope": "offline read:workout"}
INVALID_GRANT = {"error": "invalid_grant", "error_description": "The provided authorization grant is invalid"}
INVALID_CLIENT = {"error": "invalid_client", "error_description": "Client authentication failed"}


class WearableTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='whooper', password='pw', email='whooper@example.com')
        Profile.objects.create(user=self.user)
        # sync functions read the client id/secret from module globals populated from env
        for name in ('WHOOP_CLIENT_ID', 'WHOOP_CLIENT_SECRET', 'OURA_CLIENT_ID', 'OURA_CLIENT_SECRET',
                     'STRAVA_CLIENT_ID', 'STRAVA_CLIENT_SECRET'):
            p = mock.patch.object(views, name, f'test-{name.lower()}')
            p.start()
            self.addCleanup(p.stop)
        # Run the reconnect email inline instead of via the Celery broker.
        p = mock.patch.object(views, '_notify_reconnect_needed',
                              side_effect=lambda conn: send_wearable_reconnect_email(conn.user_id, conn.device_type))
        self.notify_mock = p.start()
        self.addCleanup(p.stop)

    def make_connection(self, device_type='whoop', expired=True, **overrides):
        now = timezone.now()
        fields = dict(
            user=self.user, device_type=device_type, is_active=True,
            access_token='A1', refresh_token='R1',
            expires_at=(now - timedelta(minutes=5)) if expired else (now + timedelta(hours=1)),
            external_user_id='22452524',
        )
        fields.update(overrides)
        return WearableConnection.objects.create(**fields)

    def patch_http(self, post=None, get=None):
        """Patch requests.post/get as used by wearables.views; returns (post_mock, get_mock)."""
        post_mock = mock.patch.object(views.requests, 'post', **(post or {})).start()
        get_mock = mock.patch.object(views.requests, 'get', **(get or {})).start()
        self.addCleanup(mock.patch.stopall)
        return post_mock, get_mock


class ClassifyRefreshFailureTests(TestCase):
    def test_gateway_and_rate_limit_errors_are_transient(self):
        self.assertEqual(classify_refresh_failure(FakeResponse(502, text='<html>502 Bad Gateway</html>')), TRANSIENT)
        self.assertEqual(classify_refresh_failure(FakeResponse(504, text='<html>504</html>')), TRANSIENT)
        self.assertEqual(classify_refresh_failure(FakeResponse(429, {"error": "rate_limited"})), TRANSIENT)

    def test_non_json_4xx_from_a_proxy_is_transient(self):
        self.assertEqual(classify_refresh_failure(FakeResponse(403, text='<html>Attention Required! | Cloudflare</html>')), TRANSIENT)

    def test_oauth_grant_errors_are_token_rejections(self):
        self.assertEqual(classify_refresh_failure(FakeResponse(400, INVALID_GRANT)), TOKEN_REJECTED)
        # ORY Hydra (Whoop) answers a used/rotated refresh token with invalid_request
        self.assertEqual(classify_refresh_failure(FakeResponse(400, {"error": "invalid_request", "error_hint": "..."})), TOKEN_REJECTED)
        self.assertEqual(classify_refresh_failure(FakeResponse(401, {"error": "token_inactive"})), TOKEN_REJECTED)
        # Strava's shape for a bad refresh token
        self.assertEqual(classify_refresh_failure(FakeResponse(400, {
            "message": "Bad Request",
            "errors": [{"resource": "RefreshToken", "field": "refresh_token", "code": "invalid"}]})), TOKEN_REJECTED)

    def test_our_credentials_being_rejected_is_a_config_error(self):
        self.assertEqual(classify_refresh_failure(FakeResponse(401, INVALID_CLIENT)), CONFIG_ERROR)
        self.assertEqual(classify_refresh_failure(FakeResponse(400, {"error": "invalid_scope"})), CONFIG_ERROR)
        self.assertEqual(classify_refresh_failure(FakeResponse(401, {
            "message": "Authorization Error",
            "errors": [{"resource": "Application", "field": "client_id", "code": "invalid"}]})), CONFIG_ERROR)


class WhoopRefreshTests(WearableTestCase):
    def test_valid_token_is_used_without_refreshing(self):
        conn = self.make_connection(expired=False)
        post, get = self.patch_http(get={'return_value': FakeResponse(200, whoop_payload())})

        result = sync_whoop_for_user(self.user, days_back=1)

        self.assertEqual(result, {"success": True, "workouts_added": 1})
        post.assert_not_called()
        self.assertEqual(get.call_args.kwargs['headers'], {'Authorization': 'Bearer A1'})
        self.assertIn('timeout', get.call_args.kwargs)
        conn.refresh_from_db()
        self.assertIsNotNone(conn.last_sync)

    def test_expired_token_is_refreshed_with_scope_offline_and_rotated(self):
        conn = self.make_connection(expired=True, refresh_failures=2)
        post, get = self.patch_http(post={'return_value': FakeResponse(200, REFRESH_OK)},
                                    get={'return_value': FakeResponse(200, whoop_payload())})

        result = sync_whoop_for_user(self.user, days_back=1)

        self.assertEqual(result["workouts_added"], 1)
        post.assert_called_once()
        self.assertEqual(post.call_args.args[0], 'https://api.prod.whoop.com/oauth/oauth2/token')
        self.assertEqual(post.call_args.kwargs['data'], {
            'grant_type': 'refresh_token', 'refresh_token': 'R1',
            'client_id': 'test-whoop_client_id', 'client_secret': 'test-whoop_client_secret',
            'scope': 'offline',
        })
        self.assertIn('timeout', post.call_args.kwargs)
        # the data call used the NEW access token
        self.assertEqual(get.call_args.kwargs['headers'], {'Authorization': 'Bearer A2'})
        conn.refresh_from_db()
        self.assertEqual((conn.access_token, conn.refresh_token), ('A2', 'R2'))
        self.assertGreater(conn.expires_at, timezone.now() + timedelta(minutes=55))
        self.assertEqual(conn.refresh_failures, 0, "a successful refresh resets the strike counter")
        self.assertTrue(conn.is_active)

    def test_token_about_to_expire_is_refreshed_early(self):
        self.make_connection(expires_at=timezone.now() + timedelta(seconds=60))
        post, _ = self.patch_http(post={'return_value': FakeResponse(200, REFRESH_OK)},
                                  get={'return_value': FakeResponse(200, {"records": []})})
        sync_whoop_for_user(self.user, days_back=1)
        post.assert_called_once()

    def test_transient_502_from_token_endpoint_does_not_deactivate(self):
        conn = self.make_connection(expired=True)
        post, get = self.patch_http(post={'return_value': FakeResponse(502, text='<html>502 Bad Gateway</html>')})

        with self.assertRaises(TokenRefreshError) as ctx:
            sync_whoop_for_user(self.user, days_back=1)

        self.assertFalse(ctx.exception.deactivated)
        get.assert_not_called()
        conn.refresh_from_db()
        self.assertTrue(conn.is_active, "a 502 must NOT switch the connection off")
        self.assertEqual(conn.refresh_failures, 0, "transient failures don't count as strikes")
        self.assertEqual((conn.access_token, conn.refresh_token), ('A1', 'R1'))
        self.assertEqual(len(mail.outbox), 0)
        self.notify_mock.assert_not_called()

    def test_timeout_and_connection_errors_do_not_deactivate(self):
        conn = self.make_connection(expired=True)
        for exc in (requests.Timeout("read timed out"), requests.ConnectionError("reset")):
            with self.subTest(exc=type(exc).__name__):
                self.patch_http(post={'side_effect': exc})
                with self.assertRaises(TokenRefreshError) as ctx:
                    sync_whoop_for_user(self.user, days_back=1)
                self.assertFalse(ctx.exception.deactivated)
                conn.refresh_from_db()
                self.assertTrue(conn.is_active)
                self.assertEqual(conn.refresh_failures, 0)

    def test_repeated_definitive_rejections_deactivate_and_email_the_user(self):
        conn = self.make_connection(expired=True)
        post, get = self.patch_http(post={'return_value': FakeResponse(400, INVALID_GRANT)})

        for strike in range(1, REFRESH_FAILURE_LIMIT):
            with self.assertRaises(TokenRefreshError) as ctx:
                sync_whoop_for_user(self.user, days_back=1)
            self.assertFalse(ctx.exception.deactivated)
            conn.refresh_from_db()
            self.assertTrue(conn.is_active, f"still active after strike {strike}")
            self.assertEqual(conn.refresh_failures, strike)
            self.assertEqual(len(mail.outbox), 0)

        with self.assertRaises(TokenRefreshError) as ctx:
            sync_whoop_for_user(self.user, days_back=1)
        self.assertTrue(ctx.exception.deactivated)
        conn.refresh_from_db()
        self.assertFalse(conn.is_active)
        self.assertEqual(conn.refresh_failures, REFRESH_FAILURE_LIMIT)
        self.assertEqual(post.call_count, REFRESH_FAILURE_LIMIT)
        get.assert_not_called()

        # user was told to re-link
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.to, ['whooper@example.com'])
        self.assertIn('Whoop', msg.subject)
        self.assertIn('/connect', msg.body)

        # and the deactivated connection is no longer synced
        with self.assertRaisesMessage(Exception, "Whoop not connected"):
            sync_whoop_for_user(self.user, days_back=1)

    def test_provider_rejecting_our_client_credentials_never_deactivates(self):
        conn = self.make_connection(expired=True)
        self.patch_http(post={'return_value': FakeResponse(401, INVALID_CLIENT)})

        with self.assertRaises(TokenRefreshError) as ctx:
            sync_whoop_for_user(self.user, days_back=1)

        self.assertFalse(ctx.exception.deactivated)
        conn.refresh_from_db()
        self.assertTrue(conn.is_active)
        self.assertEqual(conn.refresh_failures, 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_missing_client_credentials_fail_before_calling_the_provider(self):
        conn = self.make_connection(expired=True)
        post, _ = self.patch_http()
        with mock.patch.object(views, 'WHOOP_CLIENT_SECRET', None):
            with self.assertRaises(TokenRefreshError):
                sync_whoop_for_user(self.user, days_back=1)
        post.assert_not_called()
        conn.refresh_from_db()
        self.assertTrue(conn.is_active)

    def test_uses_tokens_stored_by_a_concurrent_sync_instead_of_refreshing_again(self):
        # Our in-memory instance is stale (expired), but another process already refreshed
        # the row. We must reuse its tokens rather than burn the (rotated) refresh token.
        conn = self.make_connection(expired=True)
        stale = WearableConnection.objects.get(pk=conn.pk)
        WearableConnection.objects.filter(pk=conn.pk).update(
            access_token='A9', refresh_token='R9', expires_at=timezone.now() + timedelta(hours=1))
        post, _ = self.patch_http()

        fresh = ensure_fresh_token(stale, token_url='https://example/token', client_id='c',
                                   client_secret='s', provider_label='Whoop')

        post.assert_not_called()
        self.assertEqual((fresh.access_token, fresh.refresh_token), ('A9', 'R9'))

    def test_last_sync_save_does_not_clobber_tokens_written_by_another_process(self):
        conn = self.make_connection(expired=False)

        def get_and_rotate_elsewhere(url, **kwargs):
            # simulate the worker refreshing the row while this (webhook) sync is mid-flight
            WearableConnection.objects.filter(pk=conn.pk).update(access_token='A-worker', refresh_token='R-worker')
            return FakeResponse(200, {"records": []})

        self.patch_http(get={'side_effect': get_and_rotate_elsewhere})
        sync_whoop_for_user(self.user, days_back=1)

        conn.refresh_from_db()
        self.assertEqual((conn.access_token, conn.refresh_token), ('A-worker', 'R-worker'),
                         "the trailing save must only write last_sync, not stale token columns")
        self.assertIsNotNone(conn.last_sync)

    def test_401_from_data_api_forces_refresh_on_next_sync(self):
        conn = self.make_connection(expired=False)
        post, get = self.patch_http(post={'return_value': FakeResponse(200, REFRESH_OK)},
                                    get={'side_effect': [FakeResponse(401, {"error": "unauthorized"}),
                                                         FakeResponse(200, {"records": []})]})

        with self.assertRaisesMessage(Exception, "401"):
            sync_whoop_for_user(self.user, days_back=1)
        post.assert_not_called()
        conn.refresh_from_db()
        self.assertLessEqual(conn.expires_at, timezone.now())
        self.assertTrue(conn.is_active)

        sync_whoop_for_user(self.user, days_back=1)     # next tick: refresh happens
        post.assert_called_once()

    # Captured at import time, i.e. before setUp() patches the module attribute.
    _real_notify = staticmethod(views._notify_reconnect_needed)

    def test_notify_helper_queues_the_celery_task(self):
        conn = self.make_connection()
        with mock.patch.object(send_wearable_reconnect_email, 'delay') as delay:
            self._real_notify(conn)
            delay.assert_called_once_with(conn.user_id, 'whoop')

    def test_notify_helper_swallows_broker_errors(self):
        # A dead Redis must not turn "connection deactivated" into an unhandled crash.
        conn = self.make_connection()
        with mock.patch.object(send_wearable_reconnect_email, 'delay', side_effect=OSError("broker down")):
            self._real_notify(conn)  # must not raise


class OuraAndStravaSharedRefreshTests(WearableTestCase):
    def test_oura_transient_504_does_not_deactivate(self):
        conn = self.make_connection(device_type='oura', expired=True)
        self.patch_http(post={'return_value': FakeResponse(504, text='<!DOCTYPE HTML ...>504 Gateway Timeout')})
        with self.assertRaises(TokenRefreshError):
            sync_oura_for_user(self.user, days_back=1)
        conn.refresh_from_db()
        self.assertTrue(conn.is_active)
        self.assertEqual(conn.refresh_failures, 0)

    def test_oura_dead_refresh_token_is_eventually_deactivated_instead_of_retrying_forever(self):
        conn = self.make_connection(device_type='oura', expired=True)
        post, _ = self.patch_http(post={'return_value': FakeResponse(
            400, {"status": 400, "error": "invalid_request", "error_description": "Invalid request"})})
        for _ in range(REFRESH_FAILURE_LIMIT):
            with self.assertRaises(TokenRefreshError):
                sync_oura_for_user(self.user, days_back=1)
        conn.refresh_from_db()
        self.assertFalse(conn.is_active)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Oura', mail.outbox[0].subject)
        self.assertEqual(post.call_args.args[0], 'https://api.ouraring.com/oauth/token')
        self.assertNotIn('scope', post.call_args.kwargs['data'])

    def test_strava_refresh_success_and_transient_failure(self):
        conn = self.make_connection(device_type='strava', expired=True)
        post, get = self.patch_http(post={'return_value': FakeResponse(200, {
            "access_token": "SA2", "refresh_token": "SR2", "expires_in": 21600})},
            get={'return_value': FakeResponse(200, [])})
        sync_strava_for_user(self.user, days_back=1)
        self.assertEqual(post.call_args.args[0], 'https://www.strava.com/oauth/token')
        conn.refresh_from_db()
        self.assertEqual((conn.access_token, conn.refresh_token), ('SA2', 'SR2'))

        WearableConnection.objects.filter(pk=conn.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        self.patch_http(post={'return_value': FakeResponse(503, text='Service Unavailable')})
        with self.assertRaises(TokenRefreshError):
            sync_strava_for_user(self.user, days_back=1)
        conn.refresh_from_db()
        self.assertTrue(conn.is_active)


class ReconnectEmailTaskTests(WearableTestCase):
    def test_sends_reconnect_email(self):
        send_wearable_reconnect_email(self.user.id, 'whoop')
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.subject, "Your Whoop connection needs to be re-linked")
        self.assertIn("Whoop", msg.body)
        self.assertIn("/connect", msg.body)
        self.assertIn("Unsubscribe", msg.body)

    def test_respects_unsubscribe_and_missing_email(self):
        self.user.profile.email_notifications = False
        self.user.profile.save()
        send_wearable_reconnect_email(self.user.id, 'oura')
        self.assertEqual(len(mail.outbox), 0)

        self.user.profile.email_notifications = True
        self.user.profile.save()
        self.user.email = ''
        self.user.save()
        send_wearable_reconnect_email(self.user.id, 'oura')
        self.assertEqual(len(mail.outbox), 0)

    def test_unknown_user_is_ignored(self):
        send_wearable_reconnect_email(999999, 'whoop')
        self.assertEqual(len(mail.outbox), 0)


class ConnectionStatusTests(WearableTestCase):
    def test_deactivated_connection_is_reported_as_needs_reconnect(self):
        self.make_connection(device_type='whoop', is_active=False)
        self.make_connection(device_type='strava', expired=False)
        token = str(AccessToken.for_user(self.user))

        resp = self.client.get(reverse('wearables:connection_status'), HTTP_AUTHORIZATION=f'Bearer {token}')

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data['whoop'])
        self.assertTrue(data['strava'])
        self.assertEqual(data['needs_reconnect'], ['whoop'])
        self.assertEqual([c['provider'] for c in data['connections']], ['strava'])


class AddWorkoutTests(WearableTestCase):
    def setUp(self):
        super().setUp()
        self.start = timezone.now() - timedelta(hours=3)

    def add(self, provider='Whoop', workout_id='1', name='Running', activity='running',
            minutes_later=0, duration=45, intensity=10):
        return add_workout(self.user, provider, workout_id, name, activity,
                           self.start + timedelta(minutes=minutes_later), duration, intensity)

    def points(self):
        return Profile.objects.get(user=self.user).score

    def test_new_workout_is_saved_and_scored(self):
        self.assertTrue(self.add())
        workout = Cardio.objects.get(user=self.user)
        self.assertEqual((workout.activity, workout.external_id, workout.duration), ('Whoop: Running', 'whoop_1', 45))
        self.assertEqual(workout.score, views.whoop_points('running', 45, 10))
        self.assertEqual(self.points(), workout.score)

    def test_sports_are_saved_as_sport(self):
        self.assertTrue(self.add(name='Tennis', activity='tennis'))
        self.assertEqual(Sport.objects.get(user=self.user).sport, 'Whoop: Tennis')
        self.assertFalse(Cardio.objects.exists())

    def test_resynced_workout_is_not_saved_or_scored_twice(self):
        self.add()
        self.assertFalse(self.add(minutes_later=30))
        self.assertEqual(Cardio.objects.count(), 1)
        self.assertEqual(self.points(), Cardio.objects.get().score)

    def test_same_workout_from_another_device_is_skipped(self):
        self.add()
        self.assertFalse(self.add(provider='Strava', workout_id='9', name='Run', activity='run', minutes_later=4))
        self.assertFalse(self.add(provider='Oura', workout_id='8', name='tennis', activity='tennis', minutes_later=-4))
        self.assertEqual(Cardio.objects.count() + Sport.objects.count(), 1)
        self.assertEqual(self.points(), Cardio.objects.get().score)

    def test_longer_recording_updates_the_workout_but_not_its_points(self):
        self.add(duration=45)
        points = self.points()
        self.assertFalse(self.add(provider='Strava', workout_id='9', name='Run', activity='run', duration=60))
        workout = Cardio.objects.get()
        self.assertEqual((workout.duration, workout.activity, workout.external_id), (60, 'Strava: Run', 'strava_9'))
        self.assertEqual(self.points(), points)

    def test_separate_workouts_on_the_same_day_are_both_saved(self):
        self.add()
        self.assertTrue(self.add(workout_id='2', minutes_later=60))
        self.assertEqual(Cardio.objects.count(), 2)

    def test_duplicate_window_is_five_minutes_either_side(self):
        self.add()
        self.assertFalse(self.add(workout_id='2', minutes_later=5))
        self.assertFalse(self.add(workout_id='3', minutes_later=-5))
        self.assertTrue(self.add(workout_id='4', minutes_later=6))
        self.assertTrue(self.add(workout_id='5', minutes_later=-6))
        self.assertEqual(Cardio.objects.count(), 3)

    def test_shorter_recording_does_not_shrink_the_workout(self):
        self.add(duration=60)
        self.assertFalse(self.add(provider='Strava', workout_id='9', name='Run', activity='run', duration=45))
        workout = Cardio.objects.get()
        self.assertEqual((workout.duration, workout.activity, workout.external_id), (60, 'Whoop: Running', 'whoop_1'))

    def test_longer_recording_of_a_different_type_is_left_alone(self):
        # Whoop sometimes reports one session as both a cardio and a sport workout
        self.add(name='Tennis', activity='tennis', duration=45)
        self.assertFalse(self.add(workout_id='2', duration=90))
        self.assertFalse(Cardio.objects.exists())
        self.assertEqual(Sport.objects.get().duration, 45)

    def test_other_users_workouts_are_not_duplicates(self):
        other = User.objects.create_user(username='other', password='pw')
        Profile.objects.create(user=other)
        add_workout(other, 'Whoop', '1', 'Running', 'running', self.start, 45, 10)
        self.assertTrue(self.add())
        self.assertEqual(Cardio.objects.filter(user=self.user).count(), 1)
        self.assertEqual(Profile.objects.get(user=other).score, Profile.objects.get(user=self.user).score)

    def test_strava_rows_saved_with_the_old_id_prefix_are_not_duplicated(self):
        Cardio.objects.create(user=self.user, activity='Strava: Run', date=self.start, duration=30,
                              score=130, external_id='Strava_77')
        self.assertFalse(self.add(provider='Strava', workout_id=77, name='Run', activity='run', duration=30))
        self.assertEqual(Cardio.objects.count(), 1)
        self.assertEqual(self.points(), 0)

    def test_points_are_added_to_the_existing_score(self):
        Profile.objects.filter(user=self.user).update(score=500)
        self.add()
        self.assertEqual(self.points(), 500 + Cardio.objects.get().score)

    def test_walks_are_saved_as_cardio(self):
        self.assertTrue(self.add(name='Walking', activity='walking', duration=50))
        workout = Cardio.objects.get()
        self.assertEqual(workout.activity, 'Whoop: Walking')
        self.assertEqual(workout.score, views.whoop_points('walking', 50, 10))


class WorkoutSyncTests(WearableTestCase):
    def test_oura_sync_saves_a_new_workout_once(self):
        self.make_connection(device_type='oura', expired=False)
        start = timezone.now() - timedelta(hours=3)
        payload = {"data": [{"id": "o-1", "activity": "running", "intensity": "hard",
                             "start_datetime": iso(start), "end_datetime": iso(start + timedelta(minutes=40))}]}
        self.patch_http(get={'return_value': FakeResponse(200, payload)})

        self.assertEqual(sync_oura_for_user(self.user, days_back=1)["workouts_added"], 1)
        self.assertEqual(sync_oura_for_user(self.user, days_back=1)["workouts_added"], 0)

        workout = Cardio.objects.get(user=self.user)
        self.assertEqual((workout.activity, workout.external_id, workout.duration), ('Oura: running', 'oura_o-1', 40))
        self.assertEqual(Profile.objects.get(user=self.user).score, views.oura_points('running', 40, 'hard'))

    def test_strava_sync_saves_a_new_activity(self):
        self.make_connection(device_type='strava', expired=False)
        payload = [{"id": 77, "type": "Run", "start_date": iso(timezone.now() - timedelta(hours=3)),
                    "moving_time": 1800, "average_speed": 3.5}]
        self.patch_http(get={'return_value': FakeResponse(200, payload)})

        self.assertEqual(sync_strava_for_user(self.user, days_back=1)["workouts_added"], 1)

        workout = Cardio.objects.get(user=self.user)
        self.assertEqual((workout.activity, workout.external_id, workout.duration), ('Strava: Run', 'strava_77', 30))
        self.assertEqual(Profile.objects.get(user=self.user).score, views.strava_points('Run', 30, 3.5))

    def test_whoop_cardio_and_sport_copies_of_one_workout_count_once(self):
        self.make_connection(expired=False)
        start = timezone.now() - timedelta(hours=3)
        records = [{"id": f"w-{sport_id}", "sport_id": sport_id, "start": iso(start),
                    "end": iso(start + timedelta(minutes=45)), "score": {"strain": 12.0}}
                   for sport_id in (48, 0)]     # Functional Fitness + Running
        self.patch_http(get={'return_value': FakeResponse(200, {"records": records})})

        self.assertEqual(sync_whoop_for_user(self.user, days_back=1)["workouts_added"], 1)
        self.assertEqual(Cardio.objects.count() + Sport.objects.count(), 1)

    def test_whoop_short_untimed_and_brief_walking_workouts_are_skipped(self):
        self.make_connection(expired=False)
        now = timezone.now()

        def record(workout_id, sport_id, hours_ago, minutes):
            start = now - timedelta(hours=hours_ago)
            return {"id": workout_id, "sport_id": sport_id, "start": iso(start),
                    "end": iso(start + timedelta(minutes=minutes)), "score": None}

        records = [record('short', 0, 10, 9), record('stroll', 63, 8, 40), record('ruck', 52, 6, 50),
                   {"id": 'open', "sport_id": 0, "start": iso(now - timedelta(hours=4)), "end": None}]
        self.patch_http(get={'return_value': FakeResponse(200, {"records": records})})

        self.assertEqual(sync_whoop_for_user(self.user, days_back=1)["workouts_added"], 1)
        workout = Cardio.objects.get()
        self.assertEqual((workout.activity, workout.external_id), ('Whoop: Hiking/Rucking', 'whoop_ruck'))
        self.assertEqual(workout.score, views.whoop_points('hikingrucking', 50, 0))

    def test_oura_short_workouts_and_brief_walks_are_skipped(self):
        self.make_connection(device_type='oura', expired=False)
        now = timezone.now()

        def workout(workout_id, activity, hours_ago, minutes):
            start = now - timedelta(hours=hours_ago)
            return {"id": workout_id, "activity": activity, "intensity": "easy",
                    "start_datetime": iso(start), "end_datetime": iso(start + timedelta(minutes=minutes))}

        payload = {"data": [workout('short', 'running', 10, 20), workout('stroll', 'walking', 8, 40),
                            workout('walk', 'walking', 6, 50)]}
        self.patch_http(get={'return_value': FakeResponse(200, payload)})

        self.assertEqual(sync_oura_for_user(self.user, days_back=1)["workouts_added"], 1)
        self.assertEqual(Cardio.objects.get().external_id, 'oura_walk')

    def test_strava_short_or_untimed_activities_are_skipped(self):
        self.make_connection(device_type='strava', expired=False)
        now = timezone.now()
        payload = [{"id": 1, "type": "Run", "start_date": iso(now - timedelta(hours=5)), "moving_time": 540},
                   {"id": 2, "type": "Run", "start_date": iso(now - timedelta(hours=3)), "moving_time": None},
                   {"id": 3, "type": "Tennis", "start_date": iso(now - timedelta(hours=1)), "moving_time": 3600}]
        self.patch_http(get={'return_value': FakeResponse(200, payload)})

        self.assertEqual(sync_strava_for_user(self.user, days_back=1)["workouts_added"], 1)
        self.assertEqual(Sport.objects.get().external_id, 'strava_3')
        self.assertFalse(Cardio.objects.exists())


class TokenCheckTests(WearableTestCase):
    def test_token_outside_the_refresh_buffer_is_not_refreshed(self):
        self.make_connection(expires_at=timezone.now() + timedelta(minutes=3))
        post, get = self.patch_http(get={'return_value': FakeResponse(200, {"records": []})})
        sync_whoop_for_user(self.user, days_back=1)
        post.assert_not_called()
        self.assertEqual(get.call_args.kwargs['headers'], {'Authorization': 'Bearer A1'})

    def test_connection_deactivated_by_another_sync_is_not_refreshed(self):
        conn = self.make_connection(expired=True)
        stale = WearableConnection.objects.get(pk=conn.pk)
        WearableConnection.objects.filter(pk=conn.pk).update(is_active=False)
        post, _ = self.patch_http()

        with self.assertRaises(TokenRefreshError) as ctx:
            ensure_fresh_token(stale, token_url='https://example/token', client_id='c',
                               client_secret='s', provider_label='Whoop')

        self.assertTrue(ctx.exception.deactivated)
        post.assert_not_called()

    def test_refresh_without_a_new_refresh_token_keeps_the_current_one(self):
        conn = self.make_connection(device_type='strava', expired=True)
        self.patch_http(post={'return_value': FakeResponse(200, {"access_token": "SA2"})},
                        get={'return_value': FakeResponse(200, [])})

        sync_strava_for_user(self.user, days_back=1)

        conn.refresh_from_db()
        self.assertEqual((conn.access_token, conn.refresh_token), ('SA2', 'R1'))
        self.assertGreater(conn.expires_at, timezone.now() + timedelta(minutes=55))

    def test_oura_and_strava_401_from_data_api_force_refresh_on_next_sync(self):
        for device_type, sync, empty in (('oura', sync_oura_for_user, {"data": []}),
                                         ('strava', sync_strava_for_user, [])):
            with self.subTest(device_type):
                conn = self.make_connection(device_type=device_type, expired=False)
                post, _ = self.patch_http(post={'return_value': FakeResponse(200, REFRESH_OK)},
                                          get={'side_effect': [FakeResponse(401, {"error": "unauthorized"}),
                                                               FakeResponse(200, empty)]})

                with self.assertRaisesMessage(Exception, "401"):
                    sync(self.user, days_back=1)
                post.assert_not_called()
                conn.refresh_from_db()
                self.assertLessEqual(conn.expires_at, timezone.now())
                self.assertTrue(conn.is_active)

                sync(self.user, days_back=1)
                post.assert_called_once()
                conn.refresh_from_db()
                self.assertEqual(conn.access_token, 'A2')

    def test_sync_endpoints_require_a_valid_bearer_token(self):
        valid = str(AccessToken.for_user(self.user))
        for name in ('wearables:sync_oura', 'wearables:sync_strava', 'wearables:sync_whoop'):
            with self.subTest(name):
                self.assertEqual(self.client.post(reverse(name)).status_code, 401)
                for header in ('Bearer not-a-jwt', f'Token {valid}', 'Bearer '):
                    resp = self.client.post(reverse(name), HTTP_AUTHORIZATION=header)
                    self.assertEqual(resp.status_code, 401, header)
                # authenticated but not connected: past the auth check
                resp = self.client.post(reverse(name), HTTP_AUTHORIZATION=f'Bearer {valid}')
                self.assertEqual(resp.status_code, 400)
                self.assertIn('not connected', resp.json()['error'].lower())

    def test_token_for_a_deleted_user_is_rejected(self):
        token = str(AccessToken.for_user(self.user))
        self.user.delete()
        resp = self.client.post(reverse('wearables:sync_whoop'), HTTP_AUTHORIZATION=f'Bearer {token}')
        self.assertEqual(resp.status_code, 401)
