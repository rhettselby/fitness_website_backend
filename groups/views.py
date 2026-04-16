from django.http import JsonResponse
from groups.models import FitnessGroup
from rest_framework_simplejwt.tokens import AccessToken
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.models import User




def get_user_from_token(request):
    auth_header = request.headers.get("Authorization", "")

    if not auth_header.startswith("Bearer "):
        return None

    token = auth_header.split(" ")[1]

    try:
        access_token = AccessToken(token)
        user_id = access_token["user_id"]
        #User is Django's built in User model
        return User.objects.get(id=user_id)
    
    except (InvalidToken, TokenError, User.DoesNotExist):
        return None




@csrf_exempt
@api_view(['GET'])
def view_groups(request):

    try:
        user = get_user_from_token(request)

        if not user:
            return JsonResponse({"error": "Authentication Required"}, status=401)
        
        groups = user.fitness_group.all()
        group_names = []

        for group in groups:
            group_names.append({"name": group.name, "id": group.id, "motto": group.motto})

        return JsonResponse({"groups": group_names})
    
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)




@csrf_exempt
@api_view(['GET'])
def get_leaderboard(request, group_id):
    try:
        user = get_user_from_token(request)

        if not user:
            return JsonResponse({"error": "Authentication Required", }, status=401)
        
        try:
            group = FitnessGroup.objects.get(id=group_id)

        except FitnessGroup.DoesNotExist:
            return JsonResponse({"error": "Group not found"}, status = 404)


        members = group.members.all()

        result = {}
        for member in members:
            score = member.profile.score
            result[member.id] = (score, member.username)
        
        #sort by score in descending order
        sorted_result = sorted(result.items(), key=lambda x: x[1][0], reverse=True)
        leaderboard = []
        for i in range(len(sorted_result)):
            user_id, info = sorted_result[i]
            score = info[0]
            username = info[1]
            leaderboard.append({"rank": i + 1, "user": username, "score": score,})

        return JsonResponse({"leaderboard": leaderboard})
    
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)




@csrf_exempt
@api_view(['POST'])
def join_group(request, group_id):
    try:
        user = get_user_from_token(request)
        if not user:
            return JsonResponse({"error": "Authentication Required"}, status=401)
        
        try:
            group = FitnessGroup.objects.get(id=group_id)
        except FitnessGroup.DoesNotExist:
            return JsonResponse({"error": "Group Not Found"}, status = 404)

        if user.fitness_group.filter(id=group_id).exists():
            return JsonResponse({"error": "User already in Group"}, status=400)
        #add group to user's ManyToManyField group
        group.members.add(user)

        return JsonResponse({"message": "User joined group"}, status=200)
    
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)





@csrf_exempt
@api_view(['POST'])
def create_group(request):
    try:
        user = get_user_from_token(request)
        if not user:
            return JsonResponse({"error": "Authentication Required"}, status=401)
        
        chosen_name = request.data.get("name")
        if not chosen_name:
            return JsonResponse({"error": "Name is required"}, status=400)
        
        motto = request.data.get("motto", None)
        
        group = FitnessGroup.objects.create(
            name = chosen_name,
            owner = user,
            motto = motto,
        )

        group.members.add(user)

        return JsonResponse({"message": "Group Created Successfully"}, status=201)

    except Exception as e:
        return JsonResponse({"error": str(e)}, status = 500)