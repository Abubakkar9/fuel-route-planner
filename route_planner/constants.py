OSRM_BASE_URL = "http://router.project-osrm.org"
OSRM_ROUTE_ENDPOINT = "{base}/route/v1/driving/{start_lon},{start_lat};{end_lon},{end_lat}"

ORS_DIRECTIONS_URL = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"

NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_USER_AGENT = "FuelRoutePlanner/1.0 (assessment@example.com)"

# Bounding box that covers all 50 US states (including Alaska and Hawaii).
# Used to reject Nominatim results that technically resolved but landed outside
# the USA — e.g. a user passing "London, UK" still geocodes, but the returned
# coordinates fall outside this box and are rejected.
# Values are intentionally loose (whole-degree boundaries) to avoid false
# rejections near borders or offshore US territories.
USA_LAT_MIN = 18.0    # southern tip of Hawaii
USA_LAT_MAX = 72.0    # northern Alaska
USA_LON_MIN = -180.0  # western Alaska (crosses the antimeridian)
USA_LON_MAX = -66.0   # eastern Maine

EARTH_RADIUS_MILES = 3959.0
MAX_RANGE_MILES = 500
MPG = 10
MAX_DEVIATION_MILES = 25
ROUTE_SAMPLE_POINTS = 500
GEOCODE_CACHE_TTL = 86400        # 1 day
ROUTE_CACHE_TTL = 3600           # 1 hour
CITY_GEOCODE_CACHE_TTL = 604800  # 7 days
