from celery import shared_task
from fitness.models import Cardio, Gym, Sport
from django.contrib.auth.models import User
from profile_page.models import Profile


@shared_task
def reset_scores():
    profiles = Profile.objects.all().update(score=0)
    print(f"New week: all ({profiles}) scores reset to 0")


import anthropic



@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def verify_workout_image(self, image_url: str, exercise: str, workout_type: str, workout_id: int, user_id: int) -> bool:
    print("image verification initiated")
    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=256,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "url", "url": image_url}},
                    {"type": "text", "text": f"""
                        Does this image provide at least minor evidence that the person in the photo had
                        been performing {exercise}? Evidence could include the location, other people, signs of activity or anything that
                        might indicate the given workout. Please be somewhat leniant, return yes or no and a brief reasoning of your decision.
                     """}
                ]
            }]
        )
    except anthropic.APIStatusError as exc:
        print(f"Claude API error during image verification: {exc}")
        # Don't retry permanent errors (bad model name, auth failures, etc.)
        if exc.status_code in (400, 401, 403, 404):
            return False
        raise self.retry(exc=exc)
    except Exception as exc:
        print(f"Claude API error during image verification: {exc}")
        raise self.retry(exc=exc)

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
    

