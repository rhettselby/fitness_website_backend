from celery import shared_task
from fitness.models import Cardio, Gym, Sport, Booze
from django.contrib.auth.models import User
from profile_page.models import Profile


@shared_task
def reset_scores():
    profiles = Profile.objects.all().update(score=0)
    print(f"New week: all ({profiles}) scores reset to 0")


import anthropic



@shared_task
def verify_workout_image(image_url: str, exercise: str, workout_type, workout_id, user_id) -> bool:
    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=256,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "url", "url": image_url}},
                {"type": "text", "text": f"""Does this image provide at least minor evidence that the person in the photo had
                been performing {exercise}? Evidence could include the location, other people, signs of activity or anything that 
                 might indicate the given workout. Please be somewhat leniant, return yes or no and a brief reasoning of your decision.  """}
            ]
        }]
    )
    result = response.content[0].text.strip().lower()
    if result.startswith("yes"):
        if workout_type == 'cardio':
            workout = Cardio.objects.get(id=workout_id)
        elif workout_type == 'gym':
            workout = Gym.objects.get(id=workout_id)
        elif workout_type == 'sport':
            workout = Sport.objects.get(id=workout_id)
        else:
            print("Workout not found during image validation")
            return True
        workout.score += 50
        workout.save()
        user = User.objects.get(id=user_id)
        user.profile.score += 50
        user.profile.save()
        return True
    
    return False
    

