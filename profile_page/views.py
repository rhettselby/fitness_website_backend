from django.http import JsonResponse
from django.shortcuts import render, redirect
from fitness.models import Cardio, Gym
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from . import forms
from .models import Profile


# Create your views here.

@login_required(login_url="/users/login/")
def viewpage(request):
    user = request.user
    cardio = Cardio.objects.filter(user = user).order_by('-date')
    gym = Gym.objects.filter(user = user).order_by('-date')
    workout_list = []
    workout_list.extend(cardio) #extend adds items form query to list, .append would add the entire query
    workout_list.extend(gym)
    workout_list = sorted(workout_list, key=lambda w: w.date, reverse=True)
    return render(request, 'profile_page/profile.html', { "workouts" : workout_list, "user" : user})

#####JSON_Version#####
@csrf_exempt
@login_required(login_url="/users/login/")
def viewpage_api(request):
    # Handle CORS preflight requests
    if request.method == 'OPTIONS':
        response = JsonResponse({})
        response['Access-Control-Allow-Origin'] = 'http://localhost:5173'
        response['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
        response['Access-Control-Allow-Headers'] = 'Content-Type'
        response['Access-Control-Allow-Credentials'] = 'true'
        return response
    
    if not request.user.is_authenticated:
        response = JsonResponse({"error": "Authentication required"}, status=401)
        response['Access-Control-Allow-Origin'] = 'http://localhost:5173'
        response['Access-Control-Allow-Credentials'] = 'true'
        return response
    
    user = request.user
    cardio = Cardio.objects.filter(user = user).order_by('-date')
    gym = Gym.objects.filter(user = user).order_by('-date')
    workout_list = []
    workout_list.extend(cardio) #extend adds items form query to list, .append would add the entire query
    workout_list.extend(gym)
    workout_list = sorted(workout_list, key=lambda w: w.date, reverse=True)

    workouts = []
    for w in workout_list:
        workouts.append({
            "id": w.id,
            "type": "cardio" if isinstance(w, Cardio) else "gym",
            "activity": w.activity,
            "date": w.date.isoformat(),
            "duration": w.duration if isinstance(w, Cardio) else None,
        })

    # Get user profile info
    try:
        profile = Profile.objects.get(user=user)
        profile_data = {
            "bio": profile.bio if profile.bio else None,
            "location": profile.location if profile.location else None,
            "birthday": profile.birthday if profile.birthday else None,
        }
    except Profile.DoesNotExist:
        profile_data = {
            "bio": None,
            "location": None,
            "birthday": None,
        }

    response = JsonResponse({
        "workouts": workouts,
        "user": {
            "id": user.id,
            "username": user.username,
        },
        "profile": profile_data
    })
    response['Access-Control-Allow-Origin'] = 'http://localhost:5173'
    response['Access-Control-Allow-Credentials'] = 'true'
    return response
    


@login_required(login_url = "/users/login/")
def editprofile(request):
    profile, _ = Profile.objects.get_or_create(user=request.user)
    if request.method == "POST":
        form = forms.EditProfile(request.POST, instance = profile)
        if form.is_valid():
            form.save()
            return redirect('profile_page:profile')
    else:
        form = forms.EditProfile(instance = profile)
    return render(request, 'profile_page/edit_profile.html', {'form' : form})

#####JSON_Version#####
@login_required(login_url = "/users/login/")
def editprofile_api(request):
    profile, _ = Profile.objects.get_or_create(user=request.user)
    if request.method == "POST":
        form = forms.EditProfile(request.POST, instance = profile)
        if form.is_valid():
            p = form.save()
            return JsonResponse({"success": True, "profile": {
                "user": {
                    "id": p.user_id,
                    "username": p.user.username,
                },
                "bio": p.bio,
                "birthday": p.birthday.isoformat,
                "location": p.location,
            }
            }, status=200)
            
    else:
        form = forms.EditProfile(instance = profile)
    return JsonResponse({"success": False, "errors": form.errors}, status = 400)

