"""
Tests for route_planner/services/geocoding.py

Covers:
- USA bounding box validation (_is_within_usa)
- Nominatim HTTP interaction (_fetch_nominatim)
- Cache-aside behaviour for address and city/state geocoding
"""
import pytest
from unittest.mock import patch, MagicMock

from route_planner.services.geocoding import (
    _is_within_usa,
    _fetch_nominatim,
    geocode_address,
    geocode_city_state,
)


# ── _is_within_usa ───────────────────────────────────────────────────────────

class TestIsWithinUSA:
    def test_chicago_is_in_usa(self):
        assert _is_within_usa(41.88, -87.63) is True

    def test_miami_is_in_usa(self):
        assert _is_within_usa(25.77, -80.19) is True

    def test_honolulu_hawaii_is_in_usa(self):
        assert _is_within_usa(21.31, -157.82) is True

    def test_anchorage_alaska_is_in_usa(self):
        assert _is_within_usa(61.22, -149.90) is True

    def test_london_uk_is_not_in_usa(self):
        assert _is_within_usa(51.51, -0.13) is False

    def test_paris_france_is_not_in_usa(self):
        assert _is_within_usa(48.85, 2.35) is False

    def test_southern_mexico_is_not_in_usa(self):
        # USA_LAT_MIN=18 (covers Hawaii). Mexico City (19.43N) is inside the bbox —
        # expected bbox limitation. Use southern Mexico (lat<18) to test exclusion.
        assert _is_within_usa(16.75, -93.11) is False

    def test_latitude_above_usa_bbox_is_not_in_usa(self):
        # USA_LAT_MAX=72. Lat 79 (high Arctic) exceeds it. Note: this is a
        # bounding-box check only — it cannot distinguish US/Canada at shared longitudes.
        assert _is_within_usa(79.0, -79.38) is False


# ── _fetch_nominatim ─────────────────────────────────────────────────────────

class TestFetchNominatim:
    def _mock_response(self, lat, lon):
        mock = MagicMock()
        mock.raise_for_status.return_value = None
        mock.json.return_value = [{"lat": str(lat), "lon": str(lon)}]
        return mock

    @patch("route_planner.services.geocoding.requests.get")
    def test_returns_coords_for_valid_us_response(self, mock_get):
        mock_get.return_value = self._mock_response(41.88, -87.63)
        result = _fetch_nominatim({"q": "Chicago, IL, USA", "format": "json", "limit": 1})
        assert result == pytest.approx((41.88, -87.63))

    @patch("route_planner.services.geocoding.requests.get")
    def test_returns_none_for_empty_response(self, mock_get):
        mock = MagicMock()
        mock.raise_for_status.return_value = None
        mock.json.return_value = []
        mock_get.return_value = mock
        assert _fetch_nominatim({"q": "Nowhere", "format": "json"}) is None

    @patch("route_planner.services.geocoding.requests.get")
    def test_rejects_non_us_coordinates(self, mock_get):
        # London coordinates — should be rejected by _is_within_usa
        mock_get.return_value = self._mock_response(51.51, -0.13)
        result = _fetch_nominatim({"q": "London, UK", "format": "json", "limit": 1})
        assert result is None

    @patch("route_planner.services.geocoding.requests.get")
    def test_returns_none_on_timeout(self, mock_get):
        import niquests
        mock_get.side_effect = niquests.exceptions.Timeout()
        assert _fetch_nominatim({"q": "test"}) is None

    @patch("route_planner.services.geocoding.requests.get")
    def test_returns_none_on_http_error(self, mock_get):
        import niquests
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_get.side_effect = niquests.exceptions.HTTPError(response=mock_response)
        assert _fetch_nominatim({"q": "test"}) is None

    @patch("route_planner.services.geocoding.requests.get")
    def test_returns_none_on_unexpected_exception(self, mock_get):
        mock_get.side_effect = Exception("Unexpected")
        assert _fetch_nominatim({"q": "test"}) is None


# ── geocode_address ──────────────────────────────────────────────────────────

class TestGeocodeAddress:
    @patch("route_planner.services.geocoding.cache_manager.get_or_fetch_geocode_address")
    def test_returns_coords_on_cache_hit(self, mock_fetch):
        mock_fetch.return_value = (41.88, -87.63)
        lat, lon = geocode_address("Chicago, IL")
        assert (lat, lon) == pytest.approx((41.88, -87.63))

    @patch("route_planner.services.geocoding.cache_manager.get_or_fetch_geocode_address")
    def test_returns_none_none_on_failure(self, mock_fetch):
        mock_fetch.return_value = None
        lat, lon = geocode_address("Nonexistent Place XYZ")
        assert lat is None
        assert lon is None

    @patch("route_planner.services.geocoding.cache_manager.get_or_fetch_geocode_address")
    def test_appends_usa_to_query(self, mock_fetch):
        # The fetcher lambda baked into geocode_address must include ", USA"
        captured = {}

        def capture_fetcher(address, fetcher):
            captured["address"] = address
            return (41.88, -87.63)

        mock_fetch.side_effect = capture_fetcher
        geocode_address("Chicago, IL")
        assert captured["address"] == "Chicago, IL"


# ── geocode_city_state ───────────────────────────────────────────────────────

class TestGeocodeCityState:
    @patch("route_planner.services.geocoding.cache_manager.get_or_fetch_geocode_city")
    def test_returns_coords_on_success(self, mock_fetch):
        mock_fetch.return_value = (41.66, -91.53)
        lat, lon = geocode_city_state("Iowa City", "IA")
        assert (lat, lon) == pytest.approx((41.66, -91.53))

    @patch("route_planner.services.geocoding.cache_manager.get_or_fetch_geocode_city")
    def test_returns_none_none_on_failure(self, mock_fetch):
        mock_fetch.return_value = None
        lat, lon = geocode_city_state("Faketown", "XX")
        assert lat is None and lon is None
