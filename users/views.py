from django.http import JsonResponse
from django.shortcuts import render, redirect
from .models import Users
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.contrib.auth import login, logout
from django.views.decorators.csrf import csrf_exempt
import json
from django.contrib.auth.models import User

from django.http import JsonResponse
from django.middleware.csrf import get_token

from rest_framework_simplejwt.tokens import RefreshToken, AccessToken
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError


# Create your views here.

def users_list(request):
    users = Users.objects.all().order_by('-date')
    return render(request, 'users/users_list.html', {'users': users})


#####JSON_Version#####
def users_list_api(request):
    user_list = Users.objects.all().order_by('-date')
    data = [
        {
            "id": u.id,
            "title": u.title,
            "slug": u.slug,
            "body": u.body,
            "date": u.date.isoformat() if u.date else None,
        }
        for u in user_list
    ]
    return JsonResponse({"users": data})


def register(request):
    if request.method == "POST":
        form = UserCreationForm(request.POST)
        if form.is_valid():
            login(request, form.save()) #form.save also returns a user value, like get_user
            return redirect("posts:list")
    else:
        form = UserCreationForm()
    return render(request, 'users/users_register.html', { "form" : form})


#####JSON_Version#####

def register_api_not_usingJSON(request):
    if request.method == "POST":
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
        

            return JsonResponse({ "success" : True, "user": {
                "id": user.id,
                "username": user.username,
                },

            "message": "User registered successfully"},
            status = 201
            )
    
        return JsonResponse(
            {"success": False, "errors": form.errors},
            status = 400
        )
    return JsonResponse({
        "success": False,
        "message": "Only POST method allowed",
    },
    status=405
    )

    ### JWT Authentication Version
@csrf_exempt
def register_api_jwt(request):
            if request.method == "POST":
                try:
                    data = json.loads(request.body)
                    username = data.get("username")
                    password = data.get("password")
                    email = data.get("email", "")

                    if not username or not password:
                        return JsonResponse({"success": False, "error": "Missing Fields"})
                    
                    if User.objects.filter(username=username).exists():
                        return JsonResponse({"success": False, "error": "Username already exists"})

                    user = User.objects.create_user(username=username, password=password, email = email)

                    refresh = RefreshToken.for_user(user)

                    return JsonResponse({
                        "success": True,
                        "user": {"id": user.id, "username": user.username},
                        "access": str(refresh.access_token),
                        "refresh": str(refresh),
                        "message": "User Registered Successfully"
                    }, status = 201)
                
                except Exception as e:
                    return JsonResponse({"success": False, "message": "Only Post Allowed"}, status = 405)




def login_view(request):
    if request.method == "POST":
        form = AuthenticationForm(data = request.POST)
        if form.is_valid():
            login(request, form.get_user()) #login uses a request and user value
            if 'next' in request.POST:
                return redirect(request.POST.get('next'))
            else:
                return redirect("posts:list")
    else:
        form = AuthenticationForm()
    return render(request, 'users/login.html', { "form" : form})

#####JSON_Version#####

def login_view_api(request):
    # Handle CORS preflight requests
    if request.method == 'OPTIONS':
        response = JsonResponse({})
        return response
    
    if request.method == "POST":
        try:
            if not request.body:
                response = JsonResponse({"success": False, "error": "Empty request body"}, status=400)
                return response
            
            data = json.loads(request.body)
            username = data.get("username")
            password = data.get("password")
            
            if not username or not password:
                response = JsonResponse({"success": False, "error": "Missing username or password"}, status=400)
                return response
            
            # Authenticate user
            from django.contrib.auth import authenticate
            user = authenticate(request, username=username, password=password)
            
            if user is not None:
                login(request, user)
                response = JsonResponse({
                    "success": True,
                    "user": {
                        "id": user.id,
                        "username": user.username,
                    },
                    "message": "User logged-in successfully"
                }, status=200)
                return response
            else:
                response = JsonResponse({"success": False, "error": "Invalid username or password"}, status=400)
                return response
        except json.JSONDecodeError:
            response = JsonResponse({"success": False, "error": "Invalid JSON"}, status=400)
            return response
        except Exception as e:
            response = JsonResponse({"success": False, "error": str(e)}, status=500)
            return response
    
    response = JsonResponse({
        "success": False,
        "message": "Only POST method allowed",
    }, status=405)
    return response


#### JWT Authentication verion
@csrf_exempt
def login_view_api_jwt(request):

    print("🔥 JWT LOGIN VIEW HIT")
    # Handle CORS preflight requests
    if request.method == 'OPTIONS':
        response = JsonResponse({})
        return response
    
    if request.method == "POST":
        try:
            if not request.body:
                response = JsonResponse({"success": False, "error": "Empty request body"}, status=400)
                return response
            
            data = json.loads(request.body)
            username = data.get("username")
            password = data.get("password")
            
            if not username or not password:
                response = JsonResponse({"success": False, "error": "Missing username or password"}, status=400)
                return response
            
            # Authenticate user
            from django.contrib.auth import authenticate
            user = authenticate(request, username=username, password=password)
            
            
            
            if user is not None:

                refresh = RefreshToken.for_user(user)

                response = JsonResponse({
                    "success": True,
                    "user": {
                        "id": user.id,
                        "username": user.username,
                    },
                    "access": str(refresh.access_token),
                    "refresh": str(refresh),
                    "message": "User logged-in successfully",
                }, status=200)
                return response
            else:
                response = JsonResponse({"success": False, "error": "Invalid username or password"}, status=400)
                return response
        except json.JSONDecodeError:
            response = JsonResponse({"success": False, "error": "Invalid JSON"}, status=400)
            return response
        except Exception as e:
            response = JsonResponse({"success": False, "error": str(e)}, status=500)
            return response
    
    response = JsonResponse({
        "success": False,
        "message": "Only POST method allowed",
    }, status=405)
    return response

@csrf_exempt
def register_api(request):
    # Handle CORS preflight requests
    if request.method == 'OPTIONS':
        response = JsonResponse({})
        return response
    
    if request.method == "POST":
        try:
            if not request.body:
                response = JsonResponse({"success": False, "error": "Empty request body"}, status=400)
                return response
            
            data = json.loads(request.body)
        except json.JSONDecodeError:
            response = JsonResponse({"success": False, "error": "Invalid JSON"}, status=400)
            return response
        
        username = data.get("username")
        password = data.get("password")
        email = data.get("email", "")

        if not username or not password:
            response = JsonResponse({"success": False, "error": "Missing fields"}, status=400)
            return response

        if User.objects.filter(username=username).exists():
            response = JsonResponse({"success": False, "error": "Username already taken"}, status=400)
            return response

        try:
            user = User.objects.create_user(username=username, password=password, email=email)
            login(request, user)
            response = JsonResponse({
                "success": True,
                "user": {"id": user.id, "username": user.username},
                "message": "User registered successfully"
            }, status=201)
            return response
        except Exception as e:
            response = JsonResponse({"success": False, "error": str(e)}, status=500)
            return response

    response = JsonResponse({"success": False, "message": "Only POST allowed"}, status=405)
    return response


@csrf_exempt
def check_auth_api(request):
    """Check if user is authenticated"""
    if request.method == 'OPTIONS':
        response = JsonResponse({})
        return response
    
    if request.method == 'GET':
        if request.user.is_authenticated:
            response = JsonResponse({
                "authenticated": True,
                "user": {
                    "id": request.user.id,
                    "username": request.user.username,
                }
            })
        else:
            response = JsonResponse({
                "authenticated": False,
                "user": None
            })
        return response
    
    response = JsonResponse({"success": False, "message": "Only GET allowed"}, status=405)
    return response


#### Check_auth JWT Authentication Version
@csrf_exempt
def check_auth_api_jwt(request):
    """Check if user is authenticated"""
    if request.method == 'OPTIONS':
        response = JsonResponse({})
        return response
    
    if request.method == 'GET':

        auth_header = request.headers.get('Authorization', '')
            
        if not auth_header.startswith('Bearer '):
            return JsonResponse({
                "authenticated": False,
                "user": None
            })
        token = auth_header.split(' ')[1]

        try:

            access_token = AccessToken(token)
            user_id = access_token['user_id']

            try: 
                user = User.objects.get(id = user_id)
                return JsonResponse({
                    "authenticated": True,
                    "user": {
                        "id": user.id,
                        "username": user.username,
                    }
                })
            
            except User.DoesNotExist:
                return JsonResponse({
                    "authenticated": False,
                    "user": None
                })
        except (InvalidToken, TokenError):
                #Token is Invalid/Expired
                return JsonResponse({
                    "authenticated": False,
                    "user": None
                })

    return JsonResponse({"success": False, "message": "Only GET Allowed"}, status = 405)


def logout_view(request):
    if request.method == "POST":
        logout(request)
        return redirect("posts:list")
    


#####JSON_Version#####

@csrf_exempt
def logout_view_api(request):
    # Handle CORS preflight requests
    if request.method == 'OPTIONS':
        response = JsonResponse({})
        return response
    
    if request.method == "POST":
        logout(request)
        response = JsonResponse({
            "success": True,
            "message": "User logged out successfully"
        }, status=200)
        return response

    response = JsonResponse({
        "success": False,
        "message": "Only POST method allowed"
    }, status=405)
    return response


def csrf(request):
    return JsonResponse({"csrfToken": get_token(request)})


#def register_view(request):
   # register = Users.objects.get

    #return render(request,'users/register_view.html', {'users' : user})
     
     
#def login_view(request):

   # return

#def logout_view(request):
    #return
     
#def profile_view(request):

 #   return
     

