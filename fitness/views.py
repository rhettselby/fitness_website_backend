from django.http import JsonResponse
from django.shortcuts import render, redirect
from .models import Cardio, Gym, Comment, Like
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from .forms import GymForm, CardioForm, CommentForm
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from django.shortcuts import get_object_or_404 #attemps to get an object, if it cant, then it raises an http 404 instead of server crashing
from django.contrib.auth import authenticate
from rest_framework_simplejwt.tokens import AccessToken
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from django.contrib.auth.models import User


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

@login_required(login_url = "/users/login/")
def workout_log(request):
    user = request.user
    cardio = Cardio.objects.filter(user = user).order_by('-date')
    gym = Gym.objects.filter(user = user).order_by('-date')
    workout_list = []
    workout_list.extend(cardio) #extend adds items form query to list, .append would add the entire query
    workout_list.extend(gym)
    workout_list = sorted(workout_list, key=lambda w: w.date, reverse=True)
    return render(request, 'fitness/workout_view.html', {'workouts': workout_list})

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

            
##This function can be handled by frontend
@login_required(login_url = "/users/login/")
def add_workout(request):
    return render(request, 'fitness/choose_workout.html')

@login_required(login_url = "/users/login/")
def add_gym(request):
    if request.method == "POST":
        form = GymForm(request.POST)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.user = request.user
            obj.save()
            return redirect('fitness:workout_log')
    else:
        form = GymForm()
    return render(request, 'fitness/add_workout.html', {'form' : form, 'type' : 'Gym'})

######JSON_Version#####

@csrf_exempt
@login_required(login_url = "/users/login/")
def add_gym_api(request):
    form = GymForm(request.POST)
    if form.is_valid():
        obj = form.save(commit=False)
        obj.user = request.user
        obj.save()
        

        return JsonResponse({ "success" : True, "gym": {
            "id": obj.id,
            "user": {
                "id": obj.user_id,
                "username": obj.user.username,
            },
            "activity": obj.activity,
            "date": obj.date.isoformat(),

        },
        "message": "Gym workout added successfully"},
        status = 201
        )
    
    return JsonResponse(
        {"success": False, "errors": form.errors},
        status = 400
    )
       
## JWT Authentication Version

@csrf_exempt
def add_gym_api_jwt(request):

    if request.method != "POST":
        return JsonResponse({"success": False, "error": "Only Post Allowed"}, status = 405)
    
    user = get_user_from_token(request)
    if not user:
        return JsonResponse({"success": False, "error": "Authentication Required"}, status = 401)
    
    form = GymForm(request.POST)

    if form.is_valid():
        obj = form.save(commit=False)
        obj.user = user
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
            },
            "message": "Gym workout added successfully"
        }, status = 201)
    
    return JsonResponse (
        {
        "success": False, "errors": form.errors,
        },
        status = 400)



@login_required(login_url = "/users/login/")
def add_cardio(request):
    if request.method == "POST":
        form = CardioForm(request.POST)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.user = request.user
            obj.save()
            return redirect('fitness:workout_log')
    else:
        form = CardioForm()
    return render(request, 'fitness/add_workout.html', {'form' : form, 'type' : 'Cardio'})

#####JSON_Version#####
@csrf_exempt
@login_required(login_url = "/users/login/")
def add_cardio_api(request):
    form = CardioForm(request.POST)
    if form.is_valid():
        obj = form.save(commit=False)
        obj.user = request.user
        obj.save()
        

        return JsonResponse({ "success" : True, "cardio": {
            "id": obj.id,
            "user": {
                "id": obj.user_id,
                "username": obj.user.username,
            },
            "activity": obj.activity,
            "duration": obj.duration,
            "date": obj.date.isoformat(),

        },
        "message": "Cardio workout added successfully"},
        status = 201
        )
    
    return JsonResponse(
        {"success": False, "errors": form.errors},
        status = 400
    )

### JWT Authentication Version

@csrf_exempt
def add_cardio_api_jwt(request):
    if request.method != "POST":
        return JsonResponse ({ "success": False, "error": "Only Post Allowed"}, status = 405)

    #getting user from JWT Token
    user = get_user_from_token(request)
    if not user:
        return JsonResponse({"success": False, "error": "Authentication required"}, status=401)
    
    form = CardioForm(request.POST)
    if form.is_valid():
        obj = form.save(commit=False)
        obj.user = user
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
            },
            "message": "Cardio workout added successfully"
        }, status=201)
    
    return JsonResponse(
        {"success": False, "errors": form.errors},
        status=400
    )


@login_required(login_url = "/user/login/")
def add_comment(request, workout_type, workout_id):
    if workout_type == 'cardio':
        model = Cardio
    elif workout_type == 'gym':
        model = Gym
    else:
        return redirect('fitness:workout_log')#invalid workout
    
    workout = get_object_or_404(model, pk = workout_id) #pk = "Primary Key" (fetch the row in model whose primary key equals workout_id)

    ct = ContentType.objects.get_for_model(workout)

    like_count = Like.objects.filter(content_type = ct, object_id = workout.id).count()
    user_liked = Like.objects.filter(content_type = ct, object_id = workout.id, user=request.user).exists()
    
    if request.method == "POST":
        form = CommentForm(request.POST)
        if form.is_valid():
            comment = form.save(commit=False)
            comment.user = request.user
            comment.workout = workout
            comment.save()
            return redirect('fitness:workout_log')
    else:
        form = CommentForm()
        comments = Comment.objects.filter(content_type = ContentType.objects.get_for_model(workout), object_id = workout.id)
        context = {
        'workout': workout,
        'workout_type': workout_type,
        'form': form,
        'comments': comments,
        'like_count' : like_count, 
        'user_liked' : user_liked }

        return render(request, 'fitness/details.html', context )
    


@csrf_exempt
def add_comment_api_jwt(request):

    if request.method != "POST":
        return JsonResponse({"success": False, "error": "Only Post Allowed"}, status=405)
    
    user = get_user_from_token(request)
    if not user:
        return JsonResponse({"success": False, "error": "Authentication required"}, status=401)
    
    form = CommentForm(request.POST)
    if form.is_valid():
        obj = form.save(commit=False)
        obj.user = user
        obj.save()
        
        return JsonResponse({
            "success": True,
            "comment": {
                "id": obj.id,
                "user": {
                    "id": obj.user_id,
                    "username": obj.user.username,
                },
                "content_type": obj.content_type,
                "text": obj.text,
                "workout": obj.workout,
                "created_at": obj.created_at.isoformat(),
            },
            "message": "Cardio workout added successfully"
        }, status=201)
    
    return JsonResponse(
        {"success": False, "errors": form.errors},
        status=400
    )
    

@login_required(login_url = "/users/login/") #check what the @ does
def add_like(request, workout_type, workout_id):
    if workout_type == 'cardio':
        model = Cardio
    else:
        model = Gym
    workout = get_object_or_404(model, pk = workout_id)

    ct = ContentType.objects.get_for_model(workout)
    like, created = Like.objects.get_or_create(user = request.user, content_type = ct, object_id = workout.id)

    if created:
        liked = True
    else:
        like.delete() # Unlikes when user taps Like
        liked = False

    return redirect('fitness:add_comment', workout_type=workout_type, workout_id=workout.id
    )

    
    






    