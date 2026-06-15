import logging

import niquests as requests
from django.conf import settings

from route_planner.constants import OSRM_BASE_URL, ORS_DIRECTIONS_URL
from . import cache_manager

logger = logging.getLogger(__name__)


def _parse_osrm(data: dict) -> dict:
    if data.get("code") != "Ok" or not data.get("routes"):
        raise ValueError("No route found between these locations")
    route = data["routes"][0]
    geometry = route["geometry"]
    return {
        "distance_miles": round(route["distance"] / 1609.34, 1),
        "duration_hours": round(route["duration"] / 3600, 1),
        "geometry": geometry,
        "coords": [(c[1], c[0]) for c in geometry["coordinates"]],
    }


def _parse_ors(data: dict) -> dict:
    try:
        feature = data["features"][0]
        props = feature["properties"]["summary"]
        geometry = feature["geometry"]
    except (KeyError, IndexError) as exc:
        raise ValueError(f"Unexpected ORS response structure: {exc}") from exc
    return {
        "distance_miles": round(props["distance"] / 1609.34, 1),
        "duration_hours": round(props["duration"] / 3600, 1),
        "geometry": geometry,
        "coords": [(c[1], c[0]) for c in geometry["coordinates"]],
    }


def _fetch_osrm(slat: float, slon: float, elat: float, elon: float) -> dict:
    url = f"{OSRM_BASE_URL}/route/v1/driving/{slon},{slat};{elon},{elat}"
    try:
        response = requests.get(
            url,
            params={"overview": "full", "geometries": "geojson", "steps": "false"},
            timeout=30,
        )
        response.raise_for_status()
    except requests.exceptions.Timeout:
        raise ValueError("OSRM request timed out — try again or set ORS_API_KEY")
    except requests.exceptions.HTTPError as exc:
        raise ValueError(f"OSRM returned HTTP {exc.response.status_code}") from exc
    return _parse_osrm(response.json())


def _fetch_ors(slat: float, slon: float, elat: float, elon: float, api_key: str) -> dict:
    try:
        response = requests.post(
            ORS_DIRECTIONS_URL,
            json={"coordinates": [[slon, slat], [elon, elat]]},
            headers={"Authorization": api_key, "Content-Type": "application/json"},
            timeout=30,
        )
        response.raise_for_status()
    except requests.exceptions.Timeout:
        raise ValueError("ORS request timed out")
    except requests.exceptions.HTTPError as exc:
        raise ValueError(f"ORS returned HTTP {exc.response.status_code}") from exc
    return _parse_ors(response.json())


def get_route(slat: float, slon: float, elat: float, elon: float) -> dict:
    api_key = getattr(settings, "ORS_API_KEY", "")

    def _fetch():
        if api_key:
            return _fetch_ors(slat, slon, elat, elon, api_key)
        return _fetch_osrm(slat, slon, elat, elon)

    return cache_manager.get_or_fetch_route(slat, slon, elat, elon, _fetch)
