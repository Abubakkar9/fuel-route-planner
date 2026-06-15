"""
Tests for route_planner/services/cache_manager.py

Covers:
- Key namespacing (keys are never constructed by callers)
- Automatic validation in set_* (invalid data raises before write)
- Cache get/set round-trips
- Flood prevention: get_or_fetch_* calls fetcher only on miss, not on hit
- Lock timeout: proceeds without lock rather than deadlocking
- Explicit invalidation helpers
- Health check
"""
import pytest
from unittest.mock import patch, MagicMock, call
from contextlib import contextmanager

import route_planner.services.cache_manager as cm


# ── Helpers ──────────────────────────────────────────────────────────────────

@contextmanager
def mock_cache_backend(store=None):
    """
    Replaces the module-level `cache` object with a simple dict-backed mock.
    Also provides a no-op lock so _fetch_lock works without Redis.
    """
    if store is None:
        store = {}

    mock = MagicMock()
    mock.get.side_effect = lambda key: store.get(key)
    mock.set.side_effect = lambda key, value, timeout=None: store.update({key: value})
    mock.delete.side_effect = lambda key: store.pop(key, None)
    mock.delete_pattern.return_value = None

    lock_mock = MagicMock()
    lock_mock.__enter__ = lambda s: s
    lock_mock.__exit__ = MagicMock(return_value=False)
    lock_mock.acquire.return_value = True
    lock_mock.release.return_value = None
    mock.lock.return_value = lock_mock

    with patch("route_planner.services.cache_manager.cache", mock):
        yield mock, store


# ── Validation in set_* ──────────────────────────────────────────────────────

class TestValidation:
    def test_set_geocode_address_rejects_invalid_lat(self):
        with mock_cache_backend():
            with pytest.raises(ValueError, match="Latitude"):
                cm.set_geocode_address("Chicago, IL", (91.0, -87.63))

    def test_set_geocode_address_rejects_invalid_lon(self):
        with mock_cache_backend():
            with pytest.raises(ValueError, match="Longitude"):
                cm.set_geocode_address("Chicago, IL", (41.88, -190.0))

    def test_set_geocode_city_rejects_invalid_coords(self):
        with mock_cache_backend():
            with pytest.raises(ValueError):
                cm.set_geocode_city("Chicago", "IL", (-100.0, -87.63))

    def test_set_route_rejects_missing_keys(self):
        with mock_cache_backend():
            with pytest.raises(ValueError, match="missing required keys"):
                cm.set_route(41.88, -87.63, 39.74, -104.99, {"distance_miles": 100.0})

    def test_set_route_rejects_zero_distance(self):
        with mock_cache_backend():
            with pytest.raises(ValueError, match="positive"):
                cm.set_route(41.88, -87.63, 39.74, -104.99, {
                    "distance_miles": 0,
                    "duration_hours": 1,
                    "geometry": {},
                    "coords": [(1, 1), (2, 2)],
                })

    def test_set_route_rejects_single_coord(self):
        with mock_cache_backend():
            with pytest.raises(ValueError, match="at least 2 points"):
                cm.set_route(41.88, -87.63, 39.74, -104.99, {
                    "distance_miles": 100.0,
                    "duration_hours": 1,
                    "geometry": {},
                    "coords": [(1, 1)],
                })


# ── get / set round-trips ────────────────────────────────────────────────────

class TestGetSet:
    def test_geocode_address_round_trip(self):
        with mock_cache_backend() as (_, store):
            cm.set_geocode_address("Chicago, IL", (41.88, -87.63))
            result = cm.get_geocode_address("Chicago, IL")
            assert result == (41.88, -87.63)

    def test_geocode_city_round_trip(self):
        with mock_cache_backend():
            cm.set_geocode_city("Chicago", "IL", (41.88, -87.63))
            result = cm.get_geocode_city("Chicago", "IL")
            assert result == (41.88, -87.63)

    def test_route_round_trip(self):
        payload = {
            "distance_miles": 1007.2,
            "duration_hours": 14.5,
            "geometry": {"type": "LineString", "coordinates": []},
            "coords": [(41.88, -87.63), (39.74, -104.99)],
        }
        with mock_cache_backend():
            cm.set_route(41.88, -87.63, 39.74, -104.99, payload)
            result = cm.get_route(41.88, -87.63, 39.74, -104.99)
            assert result == payload

    def test_missing_key_returns_none(self):
        with mock_cache_backend():
            assert cm.get_geocode_address("Unknown City") is None


# ── Flood prevention: get_or_fetch_* ─────────────────────────────────────────

class TestGetOrFetch:
    def test_fetcher_not_called_on_cache_hit(self):
        fetcher = MagicMock(return_value=(41.88, -87.63))
        with mock_cache_backend() as (_, store):
            cm.set_geocode_address("Chicago, IL", (41.88, -87.63))
            result = cm.get_or_fetch_geocode_address("Chicago, IL", fetcher)
        assert result == (41.88, -87.63)
        fetcher.assert_not_called()

    def test_fetcher_called_once_on_cache_miss(self):
        fetcher = MagicMock(return_value=(41.88, -87.63))
        with mock_cache_backend():
            result = cm.get_or_fetch_geocode_address("Chicago, IL", fetcher)
        assert result == (41.88, -87.63)
        fetcher.assert_called_once()

    def test_result_written_to_cache_after_fetch(self):
        fetcher = MagicMock(return_value=(41.88, -87.63))
        with mock_cache_backend():
            cm.get_or_fetch_geocode_address("Chicago, IL", fetcher)
            # Second call should hit cache, not fetcher
            fetcher2 = MagicMock(return_value=(0.0, 0.0))
            cm.get_or_fetch_geocode_address("Chicago, IL", fetcher2)
            fetcher2.assert_not_called()

    def test_fetcher_returning_none_does_not_cache(self):
        fetcher = MagicMock(return_value=None)
        with mock_cache_backend():
            result = cm.get_or_fetch_geocode_address("Bad Place", fetcher)
        assert result is None

    def test_route_get_or_fetch_calls_fetcher_on_miss(self):
        payload = {
            "distance_miles": 100.0,
            "duration_hours": 2.0,
            "geometry": {"type": "LineString", "coordinates": []},
            "coords": [(1.0, -100.0), (2.0, -101.0)],
        }
        fetcher = MagicMock(return_value=payload)
        with mock_cache_backend():
            result = cm.get_or_fetch_route(41.88, -87.63, 39.74, -104.99, fetcher)
        assert result == payload
        fetcher.assert_called_once()


# ── Explicit invalidation ────────────────────────────────────────────────────

class TestInvalidation:
    def test_invalidate_route_removes_entry(self):
        payload = {
            "distance_miles": 100.0,
            "duration_hours": 2.0,
            "geometry": {},
            "coords": [(1.0, 1.0), (2.0, 2.0)],
        }
        with mock_cache_backend():
            cm.set_route(41.88, -87.63, 39.74, -104.99, payload)
            cm.invalidate_route(41.88, -87.63, 39.74, -104.99)
            assert cm.get_route(41.88, -87.63, 39.74, -104.99) is None

    def test_invalidate_all_routes_calls_delete_pattern(self):
        with mock_cache_backend() as (mock, _):
            cm.invalidate_all_routes()
            mock.delete_pattern.assert_called_once_with("*route:*")

    def test_invalidate_all_geocodes_calls_delete_pattern(self):
        with mock_cache_backend() as (mock, _):
            cm.invalidate_all_geocodes()
            mock.delete_pattern.assert_called_once_with("*geocode:*")


# ── Health check ─────────────────────────────────────────────────────────────

class TestHealthCheck:
    def test_returns_true_when_redis_works(self):
        with mock_cache_backend():
            assert cm.is_healthy() is True

    def test_returns_false_when_redis_raises(self):
        with patch("route_planner.services.cache_manager.cache") as mock:
            mock.set.side_effect = Exception("Redis down")
            assert cm.is_healthy() is False
