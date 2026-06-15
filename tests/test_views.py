"""
Tests for route_planner/views.py

All external services (geocoding, routing, fuel optimizer) are mocked so tests
run without Postgres, Redis, or any network connection.

Covers every requirement from the assignment spec:
- Start + end location input validation
- USA-only geocoding enforcement
- Route response shape (map_data, fuel_stops, summary)
- Total fuel cost returned
- 500-mile range / 10 MPG constants exposed in summary
- HTTP status codes for all error paths
- Health endpoint
- Map HTML endpoint
"""
import pytest
from unittest.mock import patch, MagicMock
from django.contrib.auth.models import User
from rest_framework.test import APIClient


@pytest.fixture
def client(db):
    """Unauthenticated client — used by health and map view tests."""
    return APIClient()


@pytest.fixture
def auth_client(db):
    """Force-authenticated client — bypasses JWT for route view logic tests."""
    user = User.objects.create_user(username="viewtestuser", password="pass")
    c = APIClient()
    c.force_authenticate(user=user)
    return c


CHICAGO = (41.88, -87.63)
DENVER = (39.74, -104.99)

SAMPLE_ROUTE = {
    "distance_miles": 1007.2,
    "duration_hours": 14.5,
    "geometry": {
        "type": "LineString",
        "coordinates": [[-87.63, 41.88], [-104.99, 39.74]],
    },
    "coords": [(41.88, -87.63), (39.74, -104.99)],
}

SAMPLE_STOP = {
    "opis_id": 1,
    "name": "LOVES TRAVEL STOP",
    "address": "I-70 EXIT 232",
    "city": "Salina",
    "state": "KS",
    "price": 3.09,
    "lat": 38.84,
    "lon": -97.61,
    "distance_from_start": 490.0,
    "deviation_miles": 2.0,
    "stop_number": 1,
    "gallons_purchased": 38.5,
    "stop_cost": 118.97,
}


# ── /api/health/ ─────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestHealthView:
    def test_returns_200(self, client):
        with patch("route_planner.views.is_healthy", return_value=True):
            res = client.get("/api/health/")
        assert res.status_code == 200

    def test_response_contains_required_fields(self, client):
        with patch("route_planner.views.is_healthy", return_value=True):
            data = client.get("/api/health/").json()
        assert "status" in data
        assert "redis" in data
        assert "stations_loaded" in data

    def test_status_is_ok(self, client):
        with patch("route_planner.views.is_healthy", return_value=True):
            data = client.get("/api/health/").json()
        assert data["status"] == "ok"

    def test_redis_false_when_unhealthy(self, client):
        with patch("route_planner.views.is_healthy", return_value=False):
            data = client.get("/api/health/").json()
        assert data["redis"] is False


# ── /api/route/ — input validation ───────────────────────────────────────────

class TestRouteViewValidation:
    def test_missing_both_params_returns_400(self, auth_client):
        res = auth_client.get("/api/route/")
        assert res.status_code == 400
        assert "error" in res.json()

    def test_missing_end_param_returns_400(self, auth_client):
        res = auth_client.get("/api/route/?start=Chicago, IL")
        assert res.status_code == 400

    def test_missing_start_param_returns_400(self, auth_client):
        res = auth_client.get("/api/route/?end=Denver, CO")
        assert res.status_code == 400

    def test_unresolvable_start_returns_400(self, auth_client):
        with patch("route_planner.views.geocode_address", return_value=(None, None)):
            res = auth_client.get("/api/route/?start=FakePlaceXYZ&end=Denver, CO")
        assert res.status_code == 400
        assert "start" in res.json()["error"].lower()

    def test_unresolvable_end_returns_400(self, auth_client):
        with patch("route_planner.views.geocode_address", side_effect=[CHICAGO, (None, None)]):
            res = auth_client.get("/api/route/?start=Chicago, IL&end=FakePlaceXYZ")
        assert res.status_code == 400
        assert "end" in res.json()["error"].lower()

    def test_routing_failure_returns_502(self, auth_client):
        with patch("route_planner.views.geocode_address", side_effect=[CHICAGO, DENVER]):
            with patch("route_planner.views.get_route", side_effect=ValueError("OSRM timed out")):
                res = auth_client.get("/api/route/?start=Chicago, IL&end=Denver, CO")
        assert res.status_code == 502

    def test_no_reachable_stations_returns_422(self, auth_client):
        with patch("route_planner.views.geocode_address", side_effect=[CHICAGO, DENVER]):
            with patch("route_planner.views.get_route", return_value=SAMPLE_ROUTE):
                with patch("route_planner.views.find_stations_on_route", return_value=[]):
                    with patch("route_planner.views.optimize_fuel_stops",
                               side_effect=ValueError("No fuel stations found")):
                        res = auth_client.get("/api/route/?start=Chicago, IL&end=Denver, CO")
        assert res.status_code == 422


# ── /api/route/ — response shape (core requirements) ────────────────────────

class TestRouteViewResponseShape:
    def _get_route(self, auth_client, stops=None, total_cost=285.50, route=None):
        if route is None:
            route = SAMPLE_ROUTE
        stop_list = stops if stops is not None else [SAMPLE_STOP]
        with patch("route_planner.views.geocode_address", side_effect=[CHICAGO, DENVER]):
            with patch("route_planner.views.get_route", return_value=route):
                with patch("route_planner.views.find_stations_on_route", return_value=stop_list):
                    with patch("route_planner.views.optimize_fuel_stops",
                               return_value=(stop_list, total_cost)):
                        return auth_client.get("/api/route/?start=Chicago, IL&end=Denver, CO")

    def test_returns_200(self, auth_client):
        assert self._get_route(auth_client).status_code == 200

    def test_response_has_all_top_level_keys(self, auth_client):
        data = self._get_route(auth_client).json()
        assert {"route", "map_data", "fuel_stops", "summary"} == set(data.keys())

    # Requirement: return a map of the route
    def test_map_data_is_geojson_feature_collection(self, auth_client):
        data = self._get_route(auth_client).json()
        assert data["map_data"]["type"] == "FeatureCollection"
        assert isinstance(data["map_data"]["features"], list)

    def test_map_data_includes_route_line(self, auth_client):
        features = self._get_route(auth_client).json()["map_data"]["features"]
        types = [f["properties"]["type"] for f in features]
        assert "route" in types

    def test_map_data_includes_start_and_end_markers(self, auth_client):
        features = self._get_route(auth_client).json()["map_data"]["features"]
        types = [f["properties"]["type"] for f in features]
        assert "start" in types
        assert "end" in types

    def test_map_data_includes_fuel_stop_markers(self, auth_client):
        features = self._get_route(auth_client).json()["map_data"]["features"]
        types = [f["properties"]["type"] for f in features]
        assert "fuel_stop" in types

    # Requirement: return optimal fuel stop locations
    def test_fuel_stops_contain_required_fields(self, auth_client):
        data = self._get_route(auth_client).json()
        stop = data["fuel_stops"][0]
        required = {
            "stop_number", "station_name", "address", "city", "state",
            "price_per_gallon", "gallons_purchased", "stop_cost",
            "distance_from_start_miles", "coordinates",
        }
        assert required.issubset(stop.keys())

    # Requirement: total money spent on fuel at 10 MPG
    def test_summary_contains_total_fuel_cost(self, auth_client):
        data = self._get_route(auth_client, total_cost=285.50).json()
        assert data["summary"]["total_fuel_cost"] == 285.50

    # Requirement: 10 MPG assumed
    def test_summary_fuel_efficiency_is_10_mpg(self, auth_client):
        data = self._get_route(auth_client).json()
        assert data["summary"]["fuel_efficiency_mpg"] == 10

    # Requirement: 500-mile max range
    def test_summary_vehicle_range_is_500_miles(self, auth_client):
        data = self._get_route(auth_client).json()
        assert data["summary"]["vehicle_range_miles"] == 500

    def test_summary_number_of_stops_matches_fuel_stops_list(self, auth_client):
        data = self._get_route(auth_client).json()
        assert data["summary"]["number_of_stops"] == len(data["fuel_stops"])

    def test_route_contains_start_and_end_location(self, auth_client):
        data = self._get_route(auth_client).json()
        assert "start_location" in data["route"]
        assert "end_location" in data["route"]

    def test_route_contains_distance_and_duration(self, auth_client):
        data = self._get_route(auth_client).json()
        assert "total_distance_miles" in data["route"]
        assert "estimated_duration_hours" in data["route"]

    # Short trip (under 500 miles): no stops, but cost still returned
    def test_short_trip_returns_no_stops_but_nonzero_cost(self, auth_client):
        short_route = {**SAMPLE_ROUTE, "distance_miles": 300.0}
        data = self._get_route(auth_client, stops=[], total_cost=90.0, route=short_route).json()
        assert data["fuel_stops"] == []
        assert data["summary"]["total_fuel_cost"] == 90.0

    def test_total_gallons_is_sum_of_stop_gallons(self, auth_client):
        stop1 = {**SAMPLE_STOP, "stop_number": 1, "gallons_purchased": 20.0, "stop_cost": 60.0}
        stop2 = {**SAMPLE_STOP, "opis_id": 2, "stop_number": 2, "gallons_purchased": 30.0, "stop_cost": 90.0}
        data = self._get_route(auth_client, stops=[stop1, stop2], total_cost=150.0).json()
        assert data["summary"]["total_gallons_purchased"] == pytest.approx(50.0, abs=0.01)


# ── /api/map/ ────────────────────────────────────────────────────────────────

class TestMapView:
    def test_returns_html(self, client):
        res = client.get("/api/map/?start=Chicago, IL&end=Denver, CO")
        assert res.status_code == 200
        assert "text/html" in res["Content-Type"]

    def test_html_contains_leaflet(self, client):
        res = client.get("/api/map/")
        assert b"leaflet" in res.content.lower()

    def test_prefills_start_and_end_in_html(self, client):
        res = client.get("/api/map/?start=Chicago, IL&end=Denver, CO")
        assert b"Chicago, IL" in res.content
        assert b"Denver, CO" in res.content
