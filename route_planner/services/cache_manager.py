"""
Central Redis cache manager.

Flood prevention: uses a distributed Redis lock (SET NX) so that when a key
expires, only ONE request fetches the fresh value while concurrent requests
wait behind the lock, then hit the newly-written cache entry. This prevents
thundering herd against Nominatim / OSRM.

Validation runs automatically inside every set_* call — callers never touch it.
TTL is always enforced on write — callers never set timeouts manually.
Redis errors are caught and logged so the app degrades gracefully without cache.

Route cache invalidation is wired to FuelStation signals in apps.py (Django's
designated place for signal registration, after all models are ready).
"""
import logging
from contextlib import contextmanager
from typing import Any, Callable, Optional, Tuple

from django.core.cache import cache

from route_planner.constants import (
    GEOCODE_CACHE_TTL,
    CITY_GEOCODE_CACHE_TTL,
    ROUTE_CACHE_TTL,
)

logger = logging.getLogger(__name__)

_LOCK_TIMEOUT = 20       # seconds a lock can be held before auto-release
_LOCK_BLOCKING = 15      # seconds a waiter blocks before giving up


# ── Internal I/O ─────────────────────────────────────────────────────────────

def _get(key: str) -> Optional[Any]:
    try:
        return cache.get(key)
    except Exception as exc:
        logger.warning("Redis GET failed [key=%s]: %s", key, exc)
        return None


def _set(key: str, value: Any, ttl: int) -> None:
    try:
        cache.set(key, value, timeout=ttl)
    except Exception as exc:
        logger.warning("Redis SET failed [key=%s]: %s", key, exc)


def _delete(key: str) -> None:
    try:
        cache.delete(key)
    except Exception as exc:
        logger.warning("Redis DELETE failed [key=%s]: %s", key, exc)


def _delete_pattern(pattern: str) -> None:
    try:
        cache.delete_pattern(pattern)
    except Exception as exc:
        logger.warning("Redis pattern-delete failed [pattern=%s]: %s", pattern, exc)


# ── Distributed lock (flood prevention) ─────────────────────────────────────

@contextmanager
def _fetch_lock(lock_key: str):
    """
    Acquires a Redis lock for the duration of a cache miss fetch.
    If the lock cannot be acquired within _LOCK_BLOCKING seconds, yields anyway
    so the caller still gets data (at the cost of a duplicate fetch).
    """
    lock = cache.lock(f"lock:{lock_key}", timeout=_LOCK_TIMEOUT)
    acquired = False
    try:
        try:
            acquired = lock.acquire(blocking=True, blocking_timeout=_LOCK_BLOCKING)
            if not acquired:
                logger.warning("Could not acquire fetch lock for %s — proceeding without lock", lock_key)
        except Exception as exc:
            logger.warning("Redis lock error [key=%s]: %s — proceeding without lock", lock_key, exc)
        yield acquired
    finally:
        if acquired:
            try:
                lock.release()
            except Exception:
                pass


# ── Key builders (private — callers never construct keys) ────────────────────

def _key_geocode_address(address: str) -> str:
    slug = address.lower().strip().replace(" ", "_").replace(",", "").replace(".", "")
    return f"geocode:addr:{slug}"


def _key_geocode_city(city: str, state: str) -> str:
    slug = f"{state}:{city}".lower().replace(" ", "_")
    return f"geocode:city:{slug}"


def _key_route(slat: float, slon: float, elat: float, elon: float) -> str:
    return f"route:{slat:.4f}:{slon:.4f}:{elat:.4f}:{elon:.4f}"


# ── Validation (runs inside every set_* — never exposed to callers) ───────────

def _require_valid_coords(coords: Tuple[float, float]) -> None:
    lat, lon = coords
    if not (-90 <= lat <= 90):
        raise ValueError(f"Latitude out of range: {lat}")
    if not (-180 <= lon <= 180):
        raise ValueError(f"Longitude out of range: {lon}")


def _require_valid_route(data: dict) -> None:
    required = {"distance_miles", "duration_hours", "geometry", "coords"}
    missing = required - data.keys()
    if missing:
        raise ValueError(f"Route payload missing required keys: {missing}")
    if not isinstance(data["distance_miles"], (int, float)) or data["distance_miles"] <= 0:
        raise ValueError("Route distance_miles must be a positive number")
    if not isinstance(data["coords"], list) or len(data["coords"]) < 2:
        raise ValueError("Route coords must be a list with at least 2 points")


# ── Geocode cache API ────────────────────────────────────────────────────────

def get_geocode_address(address: str) -> Optional[Tuple[float, float]]:
    return _get(_key_geocode_address(address))


def set_geocode_address(address: str, coords: Tuple[float, float]) -> None:
    _require_valid_coords(coords)
    _set(_key_geocode_address(address), coords, GEOCODE_CACHE_TTL)


def get_or_fetch_geocode_address(
    address: str, fetcher: Callable[[], Optional[Tuple[float, float]]]
) -> Optional[Tuple[float, float]]:
    """
    Cache-aside with flood protection for address geocoding.
    Only one concurrent request fetches from Nominatim; others wait for the
    lock, then read the freshly-written cache entry.
    """
    result = get_geocode_address(address)
    if result:
        return result

    key = _key_geocode_address(address)
    with _fetch_lock(key):
        result = get_geocode_address(address)  # double-check after acquiring lock
        if result:
            return result
        result = fetcher()
        if result:
            set_geocode_address(address, result)
    return result


def get_geocode_city(city: str, state: str) -> Optional[Tuple[float, float]]:
    return _get(_key_geocode_city(city, state))


def set_geocode_city(city: str, state: str, coords: Tuple[float, float]) -> None:
    _require_valid_coords(coords)
    _set(_key_geocode_city(city, state), coords, CITY_GEOCODE_CACHE_TTL)


def get_or_fetch_geocode_city(
    city: str, state: str, fetcher: Callable[[], Optional[Tuple[float, float]]]
) -> Optional[Tuple[float, float]]:
    result = get_geocode_city(city, state)
    if result:
        return result

    key = _key_geocode_city(city, state)
    with _fetch_lock(key):
        result = get_geocode_city(city, state)
        if result:
            return result
        result = fetcher()
        if result:
            set_geocode_city(city, state, result)
    return result


# ── Route cache API ──────────────────────────────────────────────────────────

def get_route(slat: float, slon: float, elat: float, elon: float) -> Optional[dict]:
    return _get(_key_route(slat, slon, elat, elon))


def set_route(slat: float, slon: float, elat: float, elon: float, data: dict) -> None:
    _require_valid_route(data)
    _set(_key_route(slat, slon, elat, elon), data, ROUTE_CACHE_TTL)


def get_or_fetch_route(
    slat: float,
    slon: float,
    elat: float,
    elon: float,
    fetcher: Callable[[], dict],
) -> dict:
    """
    Cache-aside with flood protection for route lookups.
    Only one concurrent request calls OSRM/ORS; others wait then read cache.
    """
    result = get_route(slat, slon, elat, elon)
    if result:
        return result

    key = _key_route(slat, slon, elat, elon)
    with _fetch_lock(key):
        result = get_route(slat, slon, elat, elon)
        if result:
            return result
        result = fetcher()
        set_route(slat, slon, elat, elon, result)
    return result


# ── Explicit invalidation ────────────────────────────────────────────────────

def invalidate_route(slat: float, slon: float, elat: float, elon: float) -> None:
    _delete(_key_route(slat, slon, elat, elon))


def invalidate_all_routes() -> None:
    """Called automatically by the FuelStation post_save/post_delete signal."""
    logger.info("Invalidating all route caches — fuel station data changed")
    _delete_pattern("*route:*")


def invalidate_all_geocodes() -> None:
    _delete_pattern("*geocode:*")


# ── Health check ─────────────────────────────────────────────────────────────

def is_healthy() -> bool:
    try:
        cache.set("_health_ping", "1", timeout=5)
        return cache.get("_health_ping") == "1"
    except Exception:
        return False
