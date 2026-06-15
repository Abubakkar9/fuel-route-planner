import logging

from django.contrib.auth.models import User
from django.http import HttpResponse, HttpResponseRedirect
from django.template.loader import render_to_string
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiExample
from drf_spectacular.types import OpenApiTypes
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework_simplejwt.authentication import JWTAuthentication

from .models import FuelStation
from .services.geocoding import geocode_address
from .services.routing import get_route
from .services.fuel_optimizer import find_stations_on_route, optimize_fuel_stops
from .services.cache_manager import is_healthy
from .constants import MPG, MAX_RANGE_MILES

logger = logging.getLogger(__name__)

_LOCATION_PARAM = OpenApiParameter(
    name="start",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.QUERY,
    required=True,
    description="Starting location within the USA (e.g. 'Chicago, IL')",
)
_END_PARAM = OpenApiParameter(
    name="end",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.QUERY,
    required=True,
    description="Destination location within the USA (e.g. 'Denver, CO')",
)


class HealthView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["Health"],
        summary="Service health check",
        description="Returns API status, Redis connectivity, and count of geocoded fuel stations.",
        responses={
            200: {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "example": "ok"},
                    "redis": {"type": "boolean", "example": True},
                    "stations_loaded": {"type": "integer", "example": 4231},
                },
            }
        },
    )
    def get(self, request):
        return Response({
            "status": "ok",
            "redis": is_healthy(),
            "stations_loaded": FuelStation.objects.filter(geocoded=True).count(),
        })


class RouteView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Route"],
        summary="Plan a fuel-optimised route",
        description=(
            "Geocodes start and end locations, fetches a driving route, then selects "
            "the cheapest fuel stops along the way using a greedy cost algorithm. "
            "Vehicle is assumed to have a 500-mile range and 10 MPG efficiency, "
            "starting with a full tank."
        ),
        parameters=[_LOCATION_PARAM, _END_PARAM],
        examples=[
            OpenApiExample(
                "Chicago to Denver",
                value=None,
                parameter_only=("start", "end"),
                request_only=True,
                description="A classic cross-country trip requiring 2 fuel stops",
            )
        ],
        responses={
            200: {
                "type": "object",
                "properties": {
                    "route": {
                        "type": "object",
                        "properties": {
                            "start_location": {"type": "object"},
                            "end_location": {"type": "object"},
                            "total_distance_miles": {"type": "number", "example": 1007.2},
                            "estimated_duration_hours": {"type": "number", "example": 14.5},
                        },
                    },
                    "map_data": {
                        "type": "object",
                        "description": "GeoJSON FeatureCollection — route line + start/end/fuel-stop markers",
                    },
                    "fuel_stops": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "stop_number": {"type": "integer"},
                                "station_name": {"type": "string"},
                                "address": {"type": "string"},
                                "city": {"type": "string"},
                                "state": {"type": "string"},
                                "price_per_gallon": {"type": "number"},
                                "gallons_purchased": {"type": "number"},
                                "stop_cost": {"type": "number"},
                                "distance_from_start_miles": {"type": "number"},
                                "coordinates": {"type": "object"},
                            },
                        },
                    },
                    "summary": {
                        "type": "object",
                        "properties": {
                            "total_fuel_cost": {"type": "number", "example": 285.50},
                            "total_gallons_purchased": {"type": "number"},
                            "number_of_stops": {"type": "integer"},
                            "fuel_efficiency_mpg": {"type": "integer", "example": 10},
                            "vehicle_range_miles": {"type": "integer", "example": 500},
                            "note": {"type": "string"},
                        },
                    },
                },
            },
            400: {"description": "Missing or unresolvable location"},
            422: {"description": "No fuel stations found — route cannot be completed"},
            502: {"description": "Upstream routing API (OSRM/ORS) failed"},
        },
    )
    def get(self, request):
        start = request.query_params.get("start", "").strip()
        end = request.query_params.get("end", "").strip()

        if not start or not end:
            return Response(
                {"error": "Both 'start' and 'end' query parameters are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        start_lat, start_lon = geocode_address(start)
        if start_lat is None:
            return Response(
                {"error": f"Could not geocode start location: '{start}'"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        end_lat, end_lon = geocode_address(end)
        if end_lat is None:
            return Response(
                {"error": f"Could not geocode end location: '{end}'"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            route = get_route(start_lat, start_lon, end_lat, end_lon)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        except Exception as exc:
            logger.exception("Unexpected routing error: %s", exc)
            return Response(
                {"error": "Routing service unavailable. Please try again."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        stations_qs = FuelStation.objects.filter(geocoded=True).only(
            "opis_id", "name", "address", "city", "state", "retail_price", "lat", "lon"
        )

        stations_on_route = find_stations_on_route(route["coords"], stations_qs)

        try:
            stops, total_cost = optimize_fuel_stops(
                stations_on_route, route["distance_miles"]
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

        total_gallons = round(sum(s["gallons_purchased"] for s in stops), 2)

        map_features = [
            {
                "type": "Feature",
                "geometry": route["geometry"],
                "properties": {"type": "route", "color": "#0066cc"},
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [start_lon, start_lat]},
                "properties": {"type": "start", "label": start, "color": "#00aa00"},
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [end_lon, end_lat]},
                "properties": {"type": "end", "label": end, "color": "#cc0000"},
            },
        ]

        for stop in stops:
            map_features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [stop["lon"], stop["lat"]]},
                "properties": {
                    "type": "fuel_stop",
                    "stop_number": stop["stop_number"],
                    "station_name": stop["name"],
                    "price_per_gallon": stop["price"],
                    "gallons_purchased": stop["gallons_purchased"],
                    "stop_cost": stop["stop_cost"],
                },
            })

        return Response({
            "route": {
                "start_location": {"address": start, "lat": start_lat, "lon": start_lon},
                "end_location": {"address": end, "lat": end_lat, "lon": end_lon},
                "total_distance_miles": route["distance_miles"],
                "estimated_duration_hours": route["duration_hours"],
            },
            "map_data": {
                "type": "FeatureCollection",
                "features": map_features,
            },
            "fuel_stops": [
                {
                    "stop_number": s["stop_number"],
                    "station_name": s["name"],
                    "address": s["address"],
                    "city": s["city"],
                    "state": s["state"],
                    "price_per_gallon": s["price"],
                    "gallons_purchased": s["gallons_purchased"],
                    "stop_cost": s["stop_cost"],
                    "distance_from_start_miles": round(s["distance_from_start"], 1),
                    "coordinates": {"lat": s["lat"], "lon": s["lon"]},
                }
                for s in stops
            ],
            "summary": {
                "total_fuel_cost": total_cost,
                "total_gallons_purchased": total_gallons,
                "number_of_stops": len(stops),
                "fuel_efficiency_mpg": MPG,
                "vehicle_range_miles": MAX_RANGE_MILES,
                "note": (
                    "Assumes vehicle starts with a full tank of fuel. "
                    if len(stops) > 0
                    else
                    "Trip is within vehicle range — no fuel stops needed. "
                    "Estimated cost uses the cheapest nearby station price."
                ),
            },
        })


class MapView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["Route"],
        summary="Interactive map",
        description="Returns an HTML page with a Leaflet.js map showing the route and fuel stops.",
        parameters=[_LOCATION_PARAM, _END_PARAM],
        responses={200: OpenApiTypes.STR},
    )
    def get(self, request):
        start = request.query_params.get("start", "").strip()
        end = request.query_params.get("end", "").strip()
        html = render_to_string("map.html", {"start": start, "end": end})
        return HttpResponse(html)


class LoginPageView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(exclude=True)
    def get(self, request):
        html = render_to_string("login.html")
        return HttpResponse(html)


class SignupPageView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(exclude=True)
    def get(self, request):
        html = render_to_string("signup.html")
        return HttpResponse(html)


class RegisterView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["Auth"],
        summary="Register a new user",
        request={
            "application/json": {
                "type": "object",
                "properties": {
                    "username": {"type": "string"},
                    "password": {"type": "string"},
                    "password2": {"type": "string"},
                },
                "required": ["username", "password", "password2"],
            }
        },
        responses={
            201: {"type": "object", "properties": {"message": {"type": "string"}}},
            400: {"description": "Validation error"},
        },
    )
    def post(self, request):
        username = request.data.get("username", "").strip()
        password = request.data.get("password", "")
        password2 = request.data.get("password2", "")

        if not username or not password:
            return Response(
                {"error": "Username and password are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if password != password2:
            return Response(
                {"error": "Passwords do not match."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(password) < 8:
            return Response(
                {"error": "Password must be at least 8 characters."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if User.objects.filter(username=username).exists():
            return Response(
                {"error": "Username already taken."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        User.objects.create_user(username=username, password=password)
        return Response({"message": "Account created. You can now sign in."}, status=status.HTTP_201_CREATED)
