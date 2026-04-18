import json
from django.http import JsonResponse
from django.shortcuts import render, redirect
from .models import Cardio, Gym, Comment, Like
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from .forms import BoozeForm, GymForm, CardioForm, CommentForm, SportForm
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from django.shortcuts import get_object_or_404 #attemps to get an object, if it cant, then it raises an http 404 instead of server crashing
from django.contrib.auth import authenticate
from rest_framework_simplejwt.tokens import AccessToken
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from django.contrib.auth.models import User



EXCLUDED_ACTIVITIES = ["walk", "walking", "hike", "hiking", "surf", "surfing", "soccer", "dancing", "dance"]
##### Helper function to calculate points for a workout #####
def points(workout_type:str, activity:str, duration:float):

    points = 100
    if workout_type == "cardio":
        if activity.lower() not in EXCLUDED_ACTIVITIES:
            points += duration
        else:
            points // 2

    elif workout_type == "gym":
        points = points
    
    elif workout_type == "sport":
        points = 50
    
    elif workout_type == "booze":
        points = 20
        points += (2 * duration)

    integer_points = int(points)
    return integer_points


### Helper function for JWT Authentication
def get_user_from_token(request):
    auth_header = request.headers.get('Authorization', '')

    if not auth_header.startswith('Bearer '):
        return None
    
    token = auth_header.split(' ')[1]

    try:
        access_token = AccessToken(token)
        user_id = access_token['user_id']
        return User.objects.get(id=user_id)
    except (InvalidToken, TokenError, User.DoesNotExist):
        return None
    
#####JSON_Version#####

@login_required(login_url="/users/login/")
def workout_log_api(request):
    user = request.user
    cardio = Cardio.objects.filter(user = user).order_by('-date')
    gym = Gym.objects.filter(user = user).order_by('-date')
    workout_list = []
    workout_list.extend(cardio) 
    workout_list = sorted(workout_list, key=lambda w: w.date, reverse=True)

    return JsonResponse({"workouts": [
    {
        "id": w.id,
        "type": "cardio" if isinstance(w, Cardio) else "gym",
        "activity": w.activity,
        "date": w.date.isoformat(),
    } for w in workout_list
]})

            
## JWT Authentication Version

@csrf_exempt
def add_gym_api_jwt(request):

    if request.method != "POST":
        return JsonResponse({"success": False, "error": "Only Post Allowed"}, status = 405)
    
    user = get_user_from_token(request)
    if not user:
        return JsonResponse({"success": False, "error": "Authentication Required"}, status = 401)
    


    #Retrieve User inputed Exercises
    body = json.loads(request.body)
    exercises = body.get('exercises', [])

    form = GymForm(body)

    if form.is_valid():
        obj = form.save(commit=False)
        obj.user = user

        workout_type = "gym"
        activity = obj.activity
        duration = 0

        score = points(workout_type, activity, duration)
        user.profile.score += score
        user.profile.save()

        #add score and exercises to workout object
        obj.score = score
        obj.exercises = exercises
        obj.save()

        return JsonResponse({
            "success": True,
            "gym": {
                "id": obj.id,
                "user": {
                    "id" : obj.user_id,
                    "username": obj.user.username,
                },
                "activity": obj.activity,
                "date": obj.date.isoformat(),
                "comment_count": obj.comment_count,
                "score": score,
                "exercises": exercises,
            },
            "message": "Gym workout added successfully"
        }, status = 201)
    
    return JsonResponse (
        {
        "success": False, "errors": form.errors,
        },
        status = 400)


### JWT Authentication Version

@csrf_exempt
def add_cardio_api_jwt(request):
    if request.method != "POST":
        return JsonResponse ({ "success": False, "error": "Only Post Allowed"}, status = 405)

    #getting user from JWT Token
    user = get_user_from_token(request)
    if not user:
        return JsonResponse({"success": False, "error": "Authentication required"}, status=401)
    
    body = json.loads(request.body)
    form = CardioForm(body)

    if form.is_valid():
        obj = form.save(commit=False)
        obj.user = user

        workout_type = "cardio"
        activity = obj.activity
        duration = obj.duration

        score = points(workout_type, activity, duration)
        user.profile.score += score
        user.profile.save()

        obj.score = score
        obj.save()
        return JsonResponse({
            "success": True,
            "cardio": {
                "id": obj.id,
                "user": {
                    "id": obj.user_id,
                    "username": obj.user.username,
                },
                "activity": obj.activity,
                "duration": obj.duration,
                "date": obj.date.isoformat(),
                "comment_count": obj.comment_count,
                "score":score,
            },
            "message": "Cardio workout added successfully"
        }, status=201)
    
    return JsonResponse(
        {"success": False, "errors": form.errors},
        status=400
    )


@csrf_exempt
def add_sport(request):
    if request.method != "POST":
        return JsonResponse ({ "success": False, "error": "Only Post Allowed"}, status = 405)

    #getting user from JWT Token
    user = get_user_from_token(request)
    if not user:
        return JsonResponse({"success": False, "error": "Authentication required"}, status=401)
    
    body = json.loads(request.body)
    form = SportForm(body)

    if form.is_valid():
        obj = form.save(commit=False)
        obj.user = user

        workout_type = "sport"
        activity = obj.activity
        duration = obj.duration

        score = points(workout_type, activity, duration)
        user.profile.score += score
        user.profile.save()

        obj.score = score
        obj.save()
        return JsonResponse({
            "success": True,
            "sport": {
                "id": obj.id,
                "user": {
                    "id": obj.user_id,
                    "username": obj.user.username,
                },
                "sport": obj.sport,
                "duration": obj.duration,
                "level": obj.level,
                "date": obj.date.isoformat(),
                "comment_count": obj.comment_count,
                "score":score,
            },
            "message": "Sport workout added successfully"
        }, status=201)
    
    return JsonResponse(
        {"success": False, "errors": form.errors},
        status=400
    )


@csrf_exempt
def add_booze(request):
    if request.method != "POST":
        return JsonResponse ({ "success": False, "error": "Only Post Allowed"}, status = 405)

    #getting user from JWT Token
    user = get_user_from_token(request)
    if not user:
        return JsonResponse({"success": False, "error": "Authentication required"}, status=401)
    
    body = json.loads(request.body)
    form = BoozeForm(body)

    if form.is_valid():
        obj = form.save(commit=False)
        obj.user = user

        workout_type = "booze"
        activity = obj.drinks
        duration = obj.number_of_drinks

        score = points(workout_type, activity, duration)
        user.profile.score += score
        user.profile.save()

        obj.score = score
        obj.save()
        return JsonResponse({
            "success": True,
            "booze": {
                "id": obj.id,
                "user": {
                    "id": obj.user_id,
                    "username": obj.user.username,
                },
                "number_of_drinks": obj.number_of_drinks,
                "drinks": obj.drinks,
                "date": obj.date.isoformat(),
                "comment_count": obj.comment_count,
                "score":score,
            },
            "message": "Booze workout added successfully"
        }, status=201)
    
    return JsonResponse(
        {"success": False, "errors": form.errors},
        status=400
    )


@csrf_exempt
def add_comment_api_jwt(request):

    if request.method != "POST":
        return JsonResponse({"success": False, "error": "Only Post Allowed"}, status=405)
    
    user = get_user_from_token(request)
    if not user:
        return JsonResponse({"success": False, "error": "Authentication required"}, status=401)
    
    try:
        data=json.loads(request.body)
        workout_id = data.get('workout_id')
        workout_type = data.get('workout_type')
        comment_text = data.get('text')
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "error": "Invalid JSON"}, status = 400)
        
    if not all([workout_id, workout_type, comment_text]):
        return JsonResponse({"success": False, "error": "Missing required fields"}, status = 400)


    if workout_type == 'cardio':
        model = Cardio
    elif workout_type == 'gym':
        model = Gym
    else:
        return JsonResponse({"success": False, "error": "Invalid workout type"}, status = 400)

    try: 

        if len(comment_text) > 200:
            return JsonResponse({"success": False, "error": "Comment too long"}, status=400)
        
        workout = get_object_or_404(model, pk=workout_id)
        ct = ContentType.objects.get_for_model(workout)
        comment = Comment.objects.create(
            user=user,
            text=comment_text,
            content_type=ct,
            object_id=workout_id,
        )
    
        return JsonResponse({
            "success": True,
            "comment": {
                "id": comment.id,
                "user": {
                    "username": comment.user.username,
                },
                "text": comment.text,
                "created_at": comment.created_at.isoformat(),
            },
            "message": "Comment added successfully"
        }, status=201)
    
    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)}, status = 500)
    

def get_comments_api(request, workout_type, workout_id):
    if workout_type == 'cardio':
        model = Cardio
    elif workout_type == 'gym':
        model = Gym
    else:
        return JsonResponse({"success": False, "error": "Invalid workout type"}, status=400)

    try:
        workout = get_object_or_404(model, pk=workout_id)
        ct = ContentType.objects.get_for_model(workout)
        
        comments = Comment.objects.filter(
            content_type=ct, 
            object_id=workout.id
        ).order_by('-created_at')
        
        comments_data = [{
            "id": comment.id,
            "user": {
                "username": comment.user.username
            },
            "text": comment.text,
            "created_at": comment.created_at.isoformat()
        } for comment in comments]
        
        return JsonResponse({
            "success": True, 
            "comments": comments_data
        })
    
    except Exception as e:
        return JsonResponse({
            "success": False, 
            "error": str(e)
        }, status=500)
    
    
    







    