from django.http import JsonResponse
from groups.models import Group
from users.models import User
from rest_framework_simplejwt.tokens import AccessToken
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated



def get_user_from_token(request):
    auth_header = request.headers.get("Authorization", "")

    if not auth_header.startswith("Bearer "):
        return None

    token = auth_header.split(" ")[1]

    try:
        access_token = AccessToken(token)
        user_id = access_token["user_id"]
        return User.objects.get(id=user_id)
    except (InvalidToken, TokenError, User.DoesNotExist):
        return None





@api_view(['GET'])
def view_groups(request):

    try:
        user = get_user_from_token(request)

        if not user:
            return JsonResponse({"error": "Authentication Required"}, status=401)
        
        groups = user.groups.all()
        group_names = []

        for group in groups:
            group_names.append({"name": group.name, "id": group.id})

        return JsonResponse({"groups": group_names})
    
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)





@api_view(['GET'])
def get_leaderboard(request, group_id):
    try:
        user = get_user_from_token(request)

        if not user:
            return JsonResponse({"error": "Authentication Required", }, status=401)
        
        try:
            group = Group.objects.get(id=group_id)

        except Group.DoesNotExist:
            return JsonResponse({"error": "Group not found"}, status = 404)


        users = group.members.all()

        result = {}
        for user in users:
            score = user.score
            result[user.title] = score
        
        #sort by score in descending order
        sorted_result = list(sorted(result.items(), key=lambda x: x[1], reverse=True))
        leaderboard = []
        for i in range(len(sorted_result)):
            username, score = sorted_result[i]
            leaderboard.append({"rank": i + 1, "user": username, "score": score})

        return JsonResponse({"leaderboard": leaderboard})
    
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)





@api_view(['POST'])
def join_group(request, group_id):
    try:
        user = get_user_from_token(request)
        if not user:
            return JsonResponse({"error": "Authentication Required"}, status=401)
        
        try:
            group = Group.objects.get(id=group_id)
        except Group.DoesNotExist:
            return JsonResponse({"error": "Group Not Found"}, status = 404)

        if user.groups.filter(id=group_id).exists():
            return JsonResponse({"error": "User already in Group"}, status=400)
        #add group to user's ManyToManyField group
        user.groups.add(group)

        group.score += 1
        group.save()

        return JsonResponse({"message": "User joined group"}, status=200)
    
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)






@api_view(['POST'])
def create_group(request):
    try:
        user = get_user_from_token(request)
        if not user:
            return JsonResponse({"error": "Authentication Required"}, status=401)
        
        chosen_name = request.data.get("name")
        if not chosen_name:
            return JsonResponse({"error": "Name is required"}, status=400)
        
        group = Group.objects.create(
            name = chosen_name,
            size = 1,
        )

        user.groups.add(group)

        return JsonResponse({"message": "Group Created Successfully"}, status=201)

    except Exception as e:
        return JsonResponse({"error": str(e)}, status = 500)