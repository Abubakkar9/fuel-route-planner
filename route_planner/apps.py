from django.apps import AppConfig
from django.db.models.signals import post_save, post_delete


class RoutePlannerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "route_planner"

    def ready(self):
        from route_planner.models import FuelStation
        from route_planner.services.cache_manager import invalidate_all_routes

        def _on_station_change(sender, **kwargs):
            invalidate_all_routes()

        post_save.connect(_on_station_change, sender=FuelStation, weak=False)
        post_delete.connect(_on_station_change, sender=FuelStation, weak=False)
