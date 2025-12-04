from django.http import JsonResponse
from django.shortcuts import render
from .models import Post
from django.contrib.auth.decorators import login_required
from . import forms
from fitness.models import Gym, Cardio

# Create your views here.

#def posts_list(request):
    #posts = Post.objects.all().order_by('-date')
    #return render(request, 'posts/posts_list.html', {'posts': posts})


def post_page(request, slug):
     post = Post.objects.all().orer_by('-date')
     return render(request, 'posts/post_page.html', {'posts': post})

#####JSON_Version#####
def post_page_api(request, slug):
     post_list = Post.objects.all().order_by('date')
     posts = []

     for p in post_list:
          posts.append({
               "id": p.id,
               "title": p.title,
               "body": p.body,
               "slug": p.slug,
               "date": p.date.isoformat(),
          })
     return JsonResponse({'posts': posts}, status=200)


@login_required(login_url = "/users/login/")
def post_new(request):
     form = forms.CreatePost()
     return render(request, 'posts/post_new.html', {'form' : form})


#####JSON_Version#####
@login_required(login_url="/users/login/")
def post_new_api(request):
    if request.method == "POST":
        form = forms.CreatePost(request.POST, request.FILES)
        if form.is_valid():
            obj = form.save()

            return JsonResponse({
                "success": True,
                "post": {
                    "id": obj.id,
                    "title": obj.title,
                    "body": obj.body,
                    "slug": obj.slug,
                    "date": obj.date.isoformat() if getattr(obj, "date", None) else None,
                },
                "message": "Post created successfully"
            }, status=201)

        # Invalid form
        return JsonResponse({"success": False, "errors": form.errors}, status=400)

    # Wrong method
    return JsonResponse({
        "success": False,
        "message": "Only POST method allowed"
    }, status=405)




@login_required(login_url = "/users/login/")
def workout_log(request):
    user = request.user
    cardio = Cardio.objects.all().order_by('-date')
    gym = Gym.objects.all().order_by('-date')
    workout_list = []
    workout_list.extend(cardio) #extend adds items form query to list, .append would add the entire query
    workout_list.extend(gym)
    workout_list = sorted(workout_list, key=lambda w: w.date, reverse=True)
    return render(request, 'fitness/workout_view.html', {'workouts': workout_list})