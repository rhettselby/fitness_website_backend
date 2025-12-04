"""
URL configuration for myBackend project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from . import views
from django.conf.urls.static import static
from django.conf import settings

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', views.homepage, name='home'),  # Add this for root URL to fix 404
    path('api/', views.homepage, name='home_api'),  # Keep this for backward compatibility
    path('leaderboard/', views.leaderboard, name='leaderboard'),  # Uncomment this
    path('about/', views.about, name='about'),  # Regular page route
    path('api/about/', views.about, name='about_api'),  # Keep API route if needed
    path('posts/', include('posts.urls')),  # Regular page routes
    path('api/posts/', include('posts.urls')),  # Keep API routes
    path('users/', include('users.urls')),  # Changed from 'api/users/' to match frontend
    path('api/users/', include('users.urls')),  # Keep API route for consistency
    # Remove line 30: path('api/register',include ('users.urls')),  # This is wrong/duplicate
    path('fitness/', include('fitness.urls')),  # Regular page routes
    path('api/fitness/', include('fitness.urls')),  # Keep API routes
    path('profile/', include('profile_page.urls')),  # Regular page routes
    path('api/profile/', include('profile_page.urls')),  # Keep API routes
    path('api/leaderboard/', views.leaderboard_api, name='leaderboard_api'),  # API endpoint
]

urlpatterns += static(settings.MEDIA_URL, document_root = settings.MEDIA_ROOT)