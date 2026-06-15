import logging
from typing import Optional, Tuple

import niquests as requests
from django.conf import settings

from route_planner.constants import (
    NOMINATIM_SEARCH_URL,
    NOMINATIM_USER_AGENT,
    USA_LAT_MIN,
    USA_LAT_MAX,
    USA_LON_MIN,
    USA_LON_MAX,
)
from . import cache_manager

logger = logging.getLogger(__name__)


def _user_agent() -> str:
    email = getattr(settings, "NOMINATIM_EMAIL", "")
    if email:
        return f"FuelRoutePlanner/1.0 ({email})"
    return NOMINATIM_USER_AGENT


def _is_within_usa(lat: float, lon: float) -> bool:
    """
    Checks that a coordinate pair falls inside a bounding box covering all 50 US
    states. This is intentionally a coarse check — it catches clearly non-US results
    (European cities, South American addresses, etc.) without being so tight that it
    rejects valid US border towns or offshore territories.

    Alaska crosses the antimeridian so LON_MIN is -180.0; any longitude outside
    [-180, -66] is definitively non-US.
    """
    return USA_LAT_MIN <= lat <= USA_LAT_MAX and USA_LON_MIN <= lon <= USA_LON_MAX


def _fetch_nominatim(params: dict) -> Optional[Tuple[float, float]]:
    try:
        response = requests.get(
            NOMINATIM_SEARCH_URL, params=params, headers={"User-Agent": _user_agent()}, timeout=10
        )
        response.raise_for_status()
        results = response.json()
        if not results:
            return None

        lat, lon = float(results[0]["lat"]), float(results[0]["lon"])

        # Reject results that resolved but landed outside the USA.
        # Nominatim respects the ", USA" suffix in the query but isn't guaranteed
        # to — a foreign city with the same name (e.g. "Springfield") could slip
        # through. Bounding-box validation is the safety net.
        if not _is_within_usa(lat, lon):
            logger.warning(
                "Nominatim returned non-US coordinates (%.4f, %.4f) for query %s — rejected",
                lat, lon, params.get("q"),
            )
            return None

        return lat, lon

    except requests.exceptions.Timeout:
        logger.warning("Nominatim request timed out [params=%s]", params)
    except requests.exceptions.HTTPError as exc:
        logger.warning("Nominatim HTTP error %s [params=%s]", exc.response.status_code, params)
    except Exception as exc:
        logger.exception("Unexpected error fetching from Nominatim [params=%s]: %s", params, exc)
    return None


def geocode_address(address: str) -> Tuple[Optional[float], Optional[float]]:
    result = cache_manager.get_or_fetch_geocode_address(
        address,
        fetcher=lambda: _fetch_nominatim({"q": f"{address}, USA", "format": "json", "limit": 1}),
    )
    if result is None:
        logger.warning("Could not geocode address within USA: %s", address)
    return result if result else (None, None)


def geocode_city_state(city: str, state: str) -> Tuple[Optional[float], Optional[float]]:
    result = cache_manager.get_or_fetch_geocode_city(
        city,
        state,
        fetcher=lambda: _fetch_nominatim({"q": f"{city}, {state}, USA", "format": "json", "limit": 1}),
    )
    if result is None:
        logger.warning("Could not geocode city within USA: %s, %s", city, state)
    return result if result else (None, None)


def geocode_city_state_batch(city: str, state: str) -> Tuple[Optional[float], Optional[float]]:
    """
    Lock-free geocode for batch/ETL use (load_fuel_data).
    The caller's in-memory city_cache handles deduplication within a run;
    the DB's geocoded=True flag handles it across runs.
    A distributed lock adds up to 15s wait per city in a single-process
    batch job and is unnecessary here.
    """
    cached = cache_manager.get_geocode_city(city, state)
    if cached:
        return cached
    result = _fetch_nominatim({"q": f"{city}, {state}, USA", "format": "json", "limit": 1})
    if result:
        cache_manager.set_geocode_city(city, state, result)
        return result
    logger.warning("Could not geocode city within USA: %s, %s", city, state)
    return None, None
