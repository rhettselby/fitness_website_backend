from celery import shared_task
from fitness.models import Cardio, Gym, Sport
from django.contrib.auth.models import User
from profile_page.models import Profile


@shared_task
def reset_scores():
    profiles = Profile.objects.all().update(score=0)
    print(f"New week: all ({profiles}) scores reset to 0")


import anthropic


# Cloudinary serves a resized copy straight from the delivery URL, so the API can be
# pointed at a downscaled image without this worker ever downloading it. Anything that
# isn't a Cloudinary delivery URL is passed through untouched.
def downscaled(image_url: str) -> str:
    marker = '/image/upload/'
    head, sep, tail = image_url.partition(marker)
    if not sep:
        return image_url
    return f"{head}{marker}w_1024,h_1024,c_limit,q_80/{tail}"


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def verify_workout_image(self, image_url: str, exercise: str, workout_type: str, workout_id: int, user_id: int) -> bool:
    print("image verification initiated")
    try:
        # image_url is the public Cloudinary URL, which the API fetches itself.
        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=256,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "url", "url": downscaled(image_url)}},
                    {"type": "text", "text": f"""
                        Does this image provide at least minor evidence that the person in the photo had
                        been performing {exercise}? Evidence could include the location, other people, signs of activity or anything that
                        might indicate the given workout. Please be somewhat leniant, return yes or no and a brief reasoning of your decision.
                     """}
                ]
            }]
        )
    # Broken credentials break every workout, not just this one. Raise so the task is
    # recorded as failed with a traceback rather than disappearing into a quiet False.
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
        print(f"Image verification is misconfigured, not retrying: {exc}")
        raise
    # Transient — the same request may well succeed on another attempt.
    except anthropic.RateLimitError as exc:
        print(f"Rate limited by the Claude API, retrying: {exc}")
        raise self.retry(exc=exc)
    except anthropic.APIConnectionError as exc:
        print(f"Could not reach the Claude API, retrying: {exc}")
        raise self.retry(exc=exc)
    # Permanent and specific to this request: a malformed call, an unknown model, or an
    # image URL Claude couldn't fetch. Another attempt would fail identically.
    except (anthropic.BadRequestError, anthropic.NotFoundError) as exc:
        print(f"Claude rejected the verification request, not retrying: {exc}")
        return False
    except anthropic.APIStatusError as exc:
        if exc.status_code >= 500:
            print(f"Claude API server error ({exc.status_code}), retrying: {exc}")
            raise self.retry(exc=exc)
        print(f"Claude API error ({exc.status_code}), not retrying: {exc}")
        return False
    except Exception as exc:
        # A bug or a missing setting in this task. Retrying buries it for 90 seconds and
        # then loses it entirely, so let it surface instead.
        print(f"Image verification errored before reaching the API, not retrying: {exc!r}")
        raise

    result = response.content[0].text.strip().lower()
    print(f"Image verification result: {result}")

    if result.startswith("yes"):
        if workout_type == 'cardio':
            workout = Cardio.objects.get(id=workout_id)
        elif workout_type == 'gym':
            workout = Gym.objects.get(id=workout_id)
        elif workout_type == 'sport':
            workout = Sport.objects.get(id=workout_id)
        else:
            print("Unknown workout type during image validation")
            return False
        workout.verified = True
        workout.score += 50
        workout.save()
        user = User.objects.get(id=user_id)
        user.profile.score += 50
        user.profile.save()
        return True

    return False
    

