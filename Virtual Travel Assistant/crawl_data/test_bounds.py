def haversine(lat1, lon1, lat2, lon2):
    import math
    R = 6371.0 # Earth radius in km
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

# Da nang center
lat, lng = 16.0544, 108.2022
# Ba Na Hills
print("Ba Na:", haversine(lat, lng, 15.9967, 107.9880))
# Hoi an
print("Hoi An:", haversine(lat, lng, 15.8801, 108.3380))
# To La Nha (Approx 15.8 N, 107.8 E)
print("La Nha?:", haversine(lat, lng, 15.8, 107.8))
