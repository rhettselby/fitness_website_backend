from django.urls import path
from . import views

app_name = 'users'

urlpatterns = [
    path('api/', views.users_list_api, name = "list"),
    path('register/', views.register, name = "register"),
    path('login/', views.login_view, name = "login"),
    path('logout/', views.logout_view, name = "logout"),
    #path('register', views.register_view, name='register'),
    #path('login', views.login_view, name='login'),
    #path('logout', views.logout_view, name='logout'),
    #path('profile', views.profile_view, name='profile'),
    path('api/register/', views.register_api, name = "register_api"),
    path('api/login/', views.login_view_api, name = "login_api"),
    path('api/logout/', views.logout_view_api, name = "logout_api"),
    path('api/check-auth/', views.check_auth_api, name = "check_auth_api"),
]
