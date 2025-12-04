from django.urls import path
from . import views

app_name = 'fitness'

urlpatterns = [
    path('', views.workout_log, name = "workout_log"),
    path('add/', views.add_workout, name = "add"),
    path('add/gym/', views.add_gym, name = "add_gym"),
    path('add/cardio/', views.add_cardio, name = "add_cardio"),
    #path('details/', views.add_comment, name = 'details'),
    path('<str:workout_type>/<int:workout_id>/', views.add_comment,
          name = 'add_comment'), #parametrized URL, dynamic route with two converts
          # captures a path segment as a string, stores ut under workout_type
          # when a URLmatches, Django calls view.py with positional keywork args
          #(view recieves extra arguements to 'request'
    path('<str:workout_type>/<int:workout_id>/like/', views.add_like, name='toggle_like'),
  # or name='add_like' if you used that name
  path('api/', views.workout_log_api, name = "workout_log"),
  path('api/add/gym/', views.add_gym_api, name = "add_gym_api"),
  path('api/add/cardio/', views.add_cardio_api, name = "add_cardio_api"),
]
