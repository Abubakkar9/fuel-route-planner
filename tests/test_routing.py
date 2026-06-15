"""
Tests for route_planner/services/routing.py

Covers:
- OSRM response parsing
- ORS response parsing
- HTTP error handling (timeout, 4xx/5xx)
- Cache-aside behaviour
- Fallback: OSRM when no ORS key, ORS when key present
"""
import pytest
from unittest.mock import patch, MagicMock

from route_planner.services.routing import (
    _parse_osrm,
    _parse_ors,
    _fetch_osrm,
    _fetch_ors,
    get_route,
)


# ── _parse_osrm ──────────────────────────────────────────────────────────────

class TestParseOSRM:
    def _valid_payload(self, distance_m=1621000, duration_s=52200):
        return {
            "code": "Ok",
            "routes": [{
                "distance": distance_m,
                "duration": duration_s,
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[-87.63, 41.88], [-104.99, 39.74]],
                },
            }],
        }

    def test_parses_distance_in_miles(self):
        result = _parse_osrm(self._valid_payload(distance_m=1609.34))
        assert result["distance_miles"] == pytest.approx(1.0, abs=0.1)

    def test_parses_duration_in_hours(self):
        result = _parse_osrm(self._valid_payload(duration_s=3600))
        assert result["duration_hours"] == pytest.approx(1.0, abs=0.1)

    def test_geometry_preserved_as_geojson(self):
        result = _parse_osrm(self._valid_payload())
        assert result["geometry"]["type"] == "LineString"

    def test_coords_converted_to_lat_lon_tuples(self):
        result = _parse_osrm(self._valid_payload())
        # OSRM returns [lon, lat]; we convert to (lat, lon)
        assert result["coords"][0] == (41.88, -87.63)

    def test_raises_on_non_ok_code(self):
        with pytest.raises(ValueError, match="No route found"):
            _parse_osrm({"code": "NoRoute", "routes": []})

    def test_raises_on_empty_routes(self):
        with pytest.raises(ValueError, match="No route found"):
            _parse_osrm({"code": "Ok", "routes": []})


# ── _parse_ors ───────────────────────────────────────────────────────────────

class TestParseORS:
    def _valid_payload(self, distance_m=1621000, duration_s=52200):
        return {
            "features": [{
                "properties": {
                    "summary": {"distance": distance_m, "duration": duration_s}
                },
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[-87.63, 41.88], [-104.99, 39.74]],
                },
            }]
        }

    def test_parses_distance_in_miles(self):
        result = _parse_ors(self._valid_payload(distance_m=1609.34))
        assert result["distance_miles"] == pytest.approx(1.0, abs=0.1)

    def test_parses_duration_in_hours(self):
        result = _parse_ors(self._valid_payload(duration_s=3600))
        assert result["duration_hours"] == pytest.approx(1.0, abs=0.1)

    def test_coords_converted_to_lat_lon_tuples(self):
        result = _parse_ors(self._valid_payload())
        assert result["coords"][0] == (41.88, -87.63)

    def test_raises_on_malformed_structure(self):
        with pytest.raises(ValueError, match="Unexpected ORS response structure"):
            _parse_ors({"features": []})


# ── _fetch_osrm ──────────────────────────────────────────────────────────────

class TestFetchOSRM:
    def _valid_osrm_response(self):
        mock = MagicMock()
        mock.raise_for_status.return_value = None
        mock.json.return_value = {
            "code": "Ok",
            "routes": [{
                "distance": 1621000,
                "duration": 52200,
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[-87.63, 41.88], [-104.99, 39.74]],
                },
            }],
        }
        return mock

    @patch("route_planner.services.routing.requests.get")
    def test_successful_fetch_returns_route(self, mock_get):
        mock_get.return_value = self._valid_osrm_response()
        result = _fetch_osrm(41.88, -87.63, 39.74, -104.99)
        assert "distance_miles" in result
        assert "coords" in result

    @patch("route_planner.services.routing.requests.get")
    def test_timeout_raises_value_error(self, mock_get):
        import niquests
        mock_get.side_effect = niquests.exceptions.Timeout()
        with pytest.raises(ValueError, match="timed out"):
            _fetch_osrm(41.88, -87.63, 39.74, -104.99)

    @patch("route_planner.services.routing.requests.get")
    def test_http_error_raises_value_error(self, mock_get):
        import niquests
        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_get.side_effect = niquests.exceptions.HTTPError(response=mock_response)
        with pytest.raises(ValueError, match="HTTP 503"):
            _fetch_osrm(41.88, -87.63, 39.74, -104.99)


# ── _fetch_ors ───────────────────────────────────────────────────────────────

class TestFetchORS:
    def _valid_ors_response(self):
        mock = MagicMock()
        mock.raise_for_status.return_value = None
        mock.json.return_value = {
            "features": [{
                "properties": {"summary": {"distance": 1621000, "duration": 52200}},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[-87.63, 41.88], [-104.99, 39.74]],
                },
            }]
        }
        return mock

    @patch("route_planner.services.routing.requests.post")
    def test_successful_fetch_returns_route(self, mock_post):
        mock_post.return_value = self._valid_ors_response()
        result = _fetch_ors(41.88, -87.63, 39.74, -104.99, api_key="test-key")
        assert "distance_miles" in result

    @patch("route_planner.services.routing.requests.post")
    def test_timeout_raises_value_error(self, mock_post):
        import niquests
        mock_post.side_effect = niquests.exceptions.Timeout()
        with pytest.raises(ValueError, match="timed out"):
            _fetch_ors(41.88, -87.63, 39.74, -104.99, api_key="key")

    @patch("route_planner.services.routing.requests.post")
    def test_http_error_raises_value_error(self, mock_post):
        import niquests
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_post.side_effect = niquests.exceptions.HTTPError(response=mock_response)
        with pytest.raises(ValueError, match="HTTP 401"):
            _fetch_ors(41.88, -87.63, 39.74, -104.99, api_key="bad-key")


# ── get_route (cache-aside + fallback) ───────────────────────────────────────

class TestGetRoute:
    SAMPLE_ROUTE = {
        "distance_miles": 1007.2,
        "duration_hours": 14.5,
        "geometry": {"type": "LineString", "coordinates": [[-87.63, 41.88]]},
        "coords": [(41.88, -87.63)],
    }

    @patch("route_planner.services.routing.cache_manager.get_or_fetch_route")
    def test_uses_cache_manager(self, mock_cache):
        mock_cache.return_value = self.SAMPLE_ROUTE
        result = get_route(41.88, -87.63, 39.74, -104.99)
        assert result == self.SAMPLE_ROUTE
        mock_cache.assert_called_once()

    @patch("route_planner.services.routing.cache_manager.get_or_fetch_route")
    @patch("route_planner.services.routing._fetch_osrm")
    def test_uses_osrm_when_no_api_key(self, mock_osrm, mock_cache):
        mock_osrm.return_value = self.SAMPLE_ROUTE

        def call_fetcher(slat, slon, elat, elon, fetcher):
            return fetcher()

        mock_cache.side_effect = call_fetcher
        with patch("route_planner.services.routing.settings") as mock_settings:
            mock_settings.ORS_API_KEY = ""
            get_route(41.88, -87.63, 39.74, -104.99)
        mock_osrm.assert_called_once()

    @patch("route_planner.services.routing.cache_manager.get_or_fetch_route")
    @patch("route_planner.services.routing._fetch_ors")
    def test_uses_ors_when_api_key_present(self, mock_ors, mock_cache):
        mock_ors.return_value = self.SAMPLE_ROUTE

        def call_fetcher(slat, slon, elat, elon, fetcher):
            return fetcher()

        mock_cache.side_effect = call_fetcher
        with patch("route_planner.services.routing.settings") as mock_settings:
            mock_settings.ORS_API_KEY = "valid-key"
            get_route(41.88, -87.63, 39.74, -104.99)
        mock_ors.assert_called_once()
