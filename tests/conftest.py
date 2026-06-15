import pytest
from route_planner.models import FuelStation


@pytest.fixture
def station_factory(db):
    """Creates FuelStation records with sensible defaults, override as needed."""
    created = []

    def _make(
        opis_id=1,
        name="Test Station",
        address="I-80 Exit 100",
        city="Iowa City",
        state="IA",
        price=3.50,
        lat=41.66,
        lon=-91.53,
        geocoded=True,
    ):
        s = FuelStation.objects.create(
            opis_id=opis_id,
            name=name,
            address=address,
            city=city,
            state=state,
            retail_price=price,
            lat=lat,
            lon=lon,
            geocoded=geocoded,
        )
        created.append(s)
        return s

    return _make


@pytest.fixture
def sample_route():
    """Minimal route payload matching the shape returned by routing.get_route()."""
    return {
        "distance_miles": 1007.2,
        "duration_hours": 14.5,
        "geometry": {
            "type": "LineString",
            "coordinates": [[-87.63, 41.88], [-104.99, 39.74]],
        },
        "coords": [(41.88, -87.63), (39.74, -104.99)],
    }


def make_station_dict(
    opis_id=1,
    distance_from_start=200.0,
    price=3.50,
    lat=41.0,
    lon=-91.0,
):
    """Returns a station dict matching the shape produced by find_stations_on_route()."""
    return {
        "opis_id": opis_id,
        "name": f"Station {opis_id}",
        "address": "I-80 Exit 100",
        "city": "Testville",
        "state": "IA",
        "price": price,
        "lat": lat,
        "lon": lon,
        "distance_from_start": distance_from_start,
        "deviation_miles": 1.0,
    }
