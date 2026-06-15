import math
from typing import List, Tuple, Dict, Any

import numpy as np

from route_planner.constants import (
    EARTH_RADIUS_MILES,
    MAX_RANGE_MILES,
    MPG,
    MAX_DEVIATION_MILES,
    ROUTE_SAMPLE_POINTS,
)


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1, lat2 = math.radians(lat1), math.radians(lat2)
    dlat = lat2 - lat1
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    # Clamp to [0, 1] to guard against floating-point values just outside the domain
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(min(a, 1.0)))


def _sample_route(coords: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    if len(coords) <= ROUTE_SAMPLE_POINTS:
        return coords
    step = len(coords) // ROUTE_SAMPLE_POINTS
    sampled = [coords[i] for i in range(0, len(coords), step)]
    if sampled[-1] != coords[-1]:
        sampled.append(coords[-1])
    return sampled


def _cumulative_distances(coords: List[Tuple[float, float]]) -> List[float]:
    dists = [0.0]
    for i in range(1, len(coords)):
        dists.append(dists[-1] + haversine(*coords[i - 1], *coords[i]))
    return dists


def find_stations_on_route(
    route_coords: List[Tuple[float, float]],
    stations: Any,  # Django QuerySet, already filtered geocoded=True
    max_deviation_miles: float = MAX_DEVIATION_MILES,
) -> List[Dict]:
    sampled = _sample_route(route_coords)
    cum_dists = _cumulative_distances(sampled)

    lats = [c[0] for c in sampled]
    lons = [c[1] for c in sampled]

    # Bounding box pre-filter — drops stations far from the route before the
    # expensive per-station haversine pass
    buf = max_deviation_miles / 69.0 + 0.5
    candidates = stations.filter(
        lat__gte=min(lats) - buf,
        lat__lte=max(lats) + buf,
        lon__gte=min(lons) - buf,
        lon__lte=max(lons) + buf,
    )

    route_lats = np.array(lats)
    route_lons = np.array(lons)
    cum_arr = np.array(cum_dists)

    result = []
    # .iterator() streams rows without caching the full result set in memory
    for s in candidates.iterator():
        dlat = np.radians(route_lats - s.lat)
        dlon = np.radians(route_lons - s.lon)
        lat1_rad = math.radians(s.lat)
        lat2_rads = np.radians(route_lats)
        a = np.sin(dlat / 2) ** 2 + math.cos(lat1_rad) * np.cos(lat2_rads) * np.sin(dlon / 2) ** 2
        dists = 2 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
        nearest_idx = int(np.argmin(dists))
        min_dist = float(dists[nearest_idx])

        if min_dist <= max_deviation_miles:
            result.append({
                "opis_id": s.opis_id,
                "name": s.name,
                "address": s.address,
                "city": s.city,
                "state": s.state,
                "price": float(s.retail_price),
                "lat": s.lat,
                "lon": s.lon,
                "distance_from_start": float(cum_arr[nearest_idx]),
                "deviation_miles": round(min_dist, 1),
            })

    return sorted(result, key=lambda x: x["distance_from_start"])


def optimize_fuel_stops(
    stations_on_route: List[Dict],
    total_distance_miles: float,
    max_range: float = MAX_RANGE_MILES,
    mpg: float = MPG,
) -> Tuple[List[Dict], float]:
    if total_distance_miles <= max_range:
        # The vehicle completes the trip without stopping — the full tank it
        # started with is enough. No fuel is purchased at any station, so
        # there are no stop records to return.
        #
        # However the requirement asks for "total money spent on fuel", which
        # still applies: the vehicle burns (distance / mpg) gallons. We
        # estimate that cost using the cheapest station on the route as a
        # proxy price. If no stations were found near the route at all, we
        # fall back to 0.0 and let the caller surface an appropriate note.
        if stations_on_route:
            cheapest_price = min(s["price"] for s in stations_on_route)
            estimated_cost = round((total_distance_miles / mpg) * cheapest_price, 2)
        else:
            estimated_cost = 0.0
        return [], estimated_cost

    current_fuel = max_range  # start with a full tank (miles of range)
    current_pos = 0.0
    total_cost = 0.0
    stops = []

    while True:
        max_reach = current_pos + current_fuel

        if max_reach >= total_distance_miles:
            break

        reachable = [
            s for s in stations_on_route
            if current_pos < s["distance_from_start"] <= max_reach
        ]

        if not reachable:
            raise ValueError(
                f"No fuel stations found within {max_range:.0f} miles after mile "
                f"{current_pos:.0f}. Trip cannot be completed."
            )

        best = min(reachable, key=lambda s: s["price"])

        miles_driven = best["distance_from_start"] - current_pos
        current_fuel -= miles_driven
        current_pos = best["distance_from_start"]

        fill_miles = max_range - current_fuel
        gallons = fill_miles / mpg
        cost = gallons * best["price"]
        total_cost += cost
        current_fuel = max_range

        stops.append({
            **best,
            "stop_number": len(stops) + 1,
            "gallons_purchased": round(gallons, 2),
            "stop_cost": round(cost, 2),
        })

    return stops, round(total_cost, 2)
