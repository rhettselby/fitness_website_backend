from django.urls import path
from . import views

app_name = 'fitness'

urlpatterns = [
    path('', views.workout_log, name = "workout_log"),
    path('add/', views.add_workout, name = "add"),
    path('add/gym/', views.add_gym, name = "add_gym"),
    path('add/cardio/', views.add_cardio, name = "add_cardio"),
    #path('details/', views.add_comment, name = 'details'),
   
  # or name='add_like' if you used that name
  path('api/', views.workout_log_api, name = "workout_log"),
  path('api/add/gym/', views.add_gym_api, name = "add_gym_api"),
  path('api/add/cardio/', views.add_cardio_api, name = "add_cardio_api"),

  #jwt authentication url's

  path('api/add/gym-jwt/', views.add_gym_api_jwt, name = "add_gym_jwt"),
  path('api/add/cardio-jwt/', views.add_cardio_api_jwt, name = "add_cardio_jwt"),

  path("api/comments/<str:workout_type>/<int:workout_id>/", views.get_comments_api, name='get_comments_api'),
  path("api/comments/", views.add_comment_api_jwt, name = 'add_comment_api_jwt'),
]
