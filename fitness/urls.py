from django.urls import path
from . import views

app_name = 'fitness'

urlpatterns = [
     #jwt authentication url's

  path('api/add/gym-jwt/', views.add_gym_api_jwt, name = "add_gym_jwt"),
  path('api/add/cardio-jwt/', views.add_cardio_api_jwt, name = "add_cardio_jwt"),

  path("api/comments/<str:workout_type>/<int:workout_id>/", views.get_comments_api, name='get_comments_api'),
  path("api/comments/", views.add_comment_api_jwt, name = 'add_comment_api_jwt'),
]
