from django.urls import path
from .views import HealthView, RouteView, MapView, RegisterView

urlpatterns = [
    path("health/", HealthView.as_view(), name="health"),
    path("route/", RouteView.as_view(), name="route"),
    path("map/", MapView.as_view(), name="map"),
    path("auth/register/", RegisterView.as_view(), name="register"),
]
