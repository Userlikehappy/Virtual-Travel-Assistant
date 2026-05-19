"""
LUNA Data Crawler — OpenStreetMap (Overpass API)
Crawl địa điểm thật từ OpenStreetMap: nhà hàng, quán cafe, địa điểm tham quan,
bảo tàng, bãi biển, chùa... cho Đà Nẵng, Hội An, Huế.

Không cần API key. Data từ cộng đồng OSM — toạ độ chính xác, tên thật.

Usage:
    pip install requests
    python crawl_data/crawl_osm.py
    python crawl_data/crawl_osm.py --city "Đà Nẵng" --limit 300
"""

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

import requests

OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)
OUTPUT_FILE = OUTPUT_DIR / "crawled_data_full.json"

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
REQUEST_DELAY = 4.0  # seconds between requests (be polite to OSM)

# ── City bounding boxes [south, west, north, east] ────────────────────────────
CITIES = {
    "Đà Nẵng": {
        "bbox": (15.95, 108.10, 16.17, 108.35),
        "center": (16.047, 108.206),
        "city": "Đà Nẵng",
    },
    "Hội An": {
        "bbox": (15.83, 108.27, 15.93, 108.42),
        "center": (15.880, 108.338),
        "city": "Hội An",
    },
    "Huế": {
        "bbox": (16.38, 107.50, 16.56, 107.72),
        "center": (16.464, 107.591),
        "city": "Huế",
    },
}

# ── OSM tag → internal schema mapping ─────────────────────────────────────────
FOOD_AMENITIES = {
    "restaurant": "food",
    "food_court": "food",
    "fast_food": "food",
    "cafe": "cafe",
    "bar": "nightlife",
    "pub": "nightlife",
    "ice_cream": "food",
    "bbq": "food",
    "seafood": "food",
}

SIGHTSEEING_TOURISM = {
    "attraction": "sightseeing",
    "museum": "museum",
    "artwork": "culture",
    "gallery": "museum",
    "viewpoint": "sightseeing",
    "theme_park": "entertainment",
    "zoo": "entertainment",
    "aquarium": "entertainment",
}

HISTORIC = {
    "monument": "heritage",
    "memorial": "heritage",
    "castle": "heritage",
    "fort": "heritage",
    "ruins": "heritage",
    "archaeological_site": "heritage",
    "building": "heritage",
    "temple": "spiritual",
    "shrine": "spiritual",
}

NATURAL = {
    "beach": "beach",
    "peak": "nature",
    "cave_entrance": "nature",
    "spring": "nature",
    "waterfall": "nature",
    "bay": "nature",
    "island": "nature",
}

AMENITY_EXTRA = {
    "place_of_worship": "spiritual",
    "marketplace": "shopping",
    "arts_centre": "culture",
    "community_centre": "culture",
    "theatre": "entertainment",
    "cinema": "entertainment",
}

CUISINE_MEAL_MAP = {
    "breakfast": ["breakfast"],
    "lunch": ["lunch"],
    "dinner": ["dinner"],
    "pho": ["breakfast", "lunch"],
    "vietnamese": ["breakfast", "lunch", "dinner"],
    "seafood": ["lunch", "dinner"],
    "regional": ["lunch", "dinner"],
    "coffee_shop": ["snack"],
    "ice_cream": ["snack"],
    "cake": ["snack"],
    "sandwich": ["breakfast", "snack"],
    "noodle": ["breakfast", "lunch"],
    "banh_mi": ["breakfast"],
    "com": ["lunch", "dinner"],
    "bun": ["breakfast", "lunch"],
}

def haversine(lat1, lng1, lat2, lng2) -> float:
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


def overpass_query(query: str, retries: int = 3) -> list:
    """Execute an Overpass QL query and return elements list."""
    for attempt in range(retries):
        try:
            resp = requests.post(
                OVERPASS_URL,
                data={"data": query},
                timeout=60,
                headers={"User-Agent": "LUNA-Tourism-Crawler/1.0"},
            )
            resp.raise_for_status()
            return resp.json().get("elements", [])
        except requests.RequestException as e:
            print(f"    Attempt {attempt+1} failed: {e}")
            if attempt < retries - 1:
                time.sleep(3 * (attempt + 1))
    return []


def build_query(bbox: tuple, filters: list[str], limit: int = 500) -> str:
    """Build an Overpass QL query for given filters within a bounding box.
    Uses node-only (faster, sufficient for POIs) with a generous timeout.
    """
    s, w, n, e = bbox
    union_parts = []
    for filt in filters:
        key, val = filt.split("=", 1)
        union_parts.append(f'  node["{key}"="{val}"]["name"]({s},{w},{n},{e});')
    union_str = "\n".join(union_parts)
    return f"""[out:json][timeout:60];
(
{union_str}
);
out {limit};"""


def parse_hours(tags: dict) -> str:
    raw = tags.get("opening_hours", "")
    if not raw:
        return ""
    # Normalize common OSM formats
    raw = raw.replace(";", " | ").replace("–", "-").replace("—", "-")
    # Extract HH:MM-HH:MM if present
    import re
    times = re.findall(r"\d{1,2}:\d{2}", raw)
    if len(times) >= 2:
        return f"{times[0]}-{times[1]}"
    if "24/7" in raw or "24 hours" in raw.lower():
        return "00:00-23:59"
    return raw[:40]


def parse_price(tags: dict) -> float:
    """Estimate price from OSM tags."""
    price_range = tags.get("price_range", "") or tags.get("charge", "")
    fee = tags.get("fee", "")
    if "no" in fee.lower() or "free" in fee.lower() or "miễn" in fee.lower():
        return 0.0

    # Vietnamese price patterns
    import re
    nums = re.findall(r"[\d]+(?:[.,][\d]+)*", price_range)
    if nums:
        try:
            # Treat dots as thousands separators
            val = float(nums[-1].replace(".", "").replace(",", ""))
            if val > 10:
                return val
        except ValueError:
            pass

    # Estimate from star rating or price_level
    level = tags.get("price_level", "")
    if level:
        try:
            n = float(level)
            return n * 80000  # rough: $$ ~160k VND
        except ValueError:
            pass

    return 0.0


def classify_environment(tags: dict, category: str) -> str:
    indoor_cats = {"museum", "cafe", "nightlife", "shopping", "entertainment"}
    outdoor_cats = {"beach", "nature", "sightseeing"}
    if category in indoor_cats:
        return "Indoor"
    if category in outdoor_cats:
        return "Outdoor"
    indoor_val = tags.get("indoor", "") or tags.get("covered", "")
    if indoor_val in ("yes", "true"):
        return "Indoor"
    # Museums, galleries typically indoor
    if tags.get("tourism") in ("museum", "gallery"):
        return "Indoor"
    if tags.get("amenity") in ("cafe", "restaurant", "bar", "pub"):
        return "Sheltered"
    return "Outdoor"


def infer_meal_time(tags: dict) -> list:
    cuisine = tags.get("cuisine", "").lower()
    name = (tags.get("name", "") + " " + tags.get("name:vi", "")).lower()

    meal_times = set()

    for keyword, meals in CUISINE_MEAL_MAP.items():
        if keyword in cuisine or keyword in name:
            meal_times.update(meals)

    # Name-based Vietnamese hints
    if any(k in name for k in ["bún", "phở", "cháo", "bánh mì", "hủ tiếu", "bánh canh"]):
        meal_times.update(["breakfast", "lunch"])
    if any(k in name for k in ["cơm", "lẩu", "hải sản", "nướng", "nhà hàng"]):
        meal_times.update(["lunch", "dinner"])
    if any(k in name for k in ["cafe", "cà phê", "kem", "chè"]):
        meal_times.update(["snack"])
    if any(k in name for k in ["bar", "pub", "beer", "bia"]):
        meal_times.update(["dinner"])

    # Infer from opening hours
    hours = tags.get("opening_hours", "")
    import re
    times = re.findall(r"(\d{1,2}):\d{2}", hours)
    if times:
        open_h = int(times[0])
        if open_h <= 9 and not meal_times:
            meal_times.add("breakfast")
        if 10 <= open_h <= 14 and not meal_times:
            meal_times.update(["lunch"])

    if not meal_times:
        meal_times = {"lunch", "dinner"}

    return list(meal_times)


def get_category(tags: dict) -> tuple[str, str]:
    """Returns (category, item_type)."""
    amenity = tags.get("amenity", "")
    tourism = tags.get("tourism", "")
    historic = tags.get("historic", "")
    natural = tags.get("natural", "")
    leisure = tags.get("leisure", "")
    shop = tags.get("shop", "")

    if amenity in FOOD_AMENITIES:
        return FOOD_AMENITIES[amenity], "food"
    if amenity in AMENITY_EXTRA:
        return AMENITY_EXTRA[amenity], "location"
    if tourism in SIGHTSEEING_TOURISM:
        return SIGHTSEEING_TOURISM[tourism], "location"
    if historic in HISTORIC:
        return HISTORIC[historic], "location"
    if natural in NATURAL:
        return NATURAL[natural], "location"
    if leisure in ("park", "garden", "beach_resort"):
        return "nature", "location"
    if leisure in ("sports_centre", "swimming_pool"):
        return "sports", "location"
    if shop:
        return "shopping", "location"
    if amenity == "place_of_worship":
        religion = tags.get("religion", "")
        return "spiritual", "location"

    return "sightseeing", "location"


def extract_name(tags: dict) -> str:
    """Prefer Vietnamese name."""
    return (
        tags.get("name:vi")
        or tags.get("name")
        or tags.get("alt_name")
        or ""
    ).strip()


def element_to_record(elem: dict, city_info: dict) -> dict | None:
    tags = elem.get("tags", {})
    name = extract_name(tags)
    if not name or len(name) < 2:
        return None

    # Get coordinates (nodes have lat/lon, ways have center)
    if elem.get("type") == "node":
        lat = elem.get("lat", 0.0)
        lng = elem.get("lon", 0.0)
    else:
        center = elem.get("center", {})
        lat = center.get("lat", 0.0)
        lng = center.get("lon", 0.0)

    if not (8 <= lat <= 24 and 102 <= lng <= 110):
        return None

    # Double-check within city radius
    dist = haversine(lat, lng, city_info["center"][0], city_info["center"][1])
    max_radius = {"Đà Nẵng": 50, "Hội An": 25, "Huế": 35}.get(city_info["city"], 40)
    if dist > max_radius:
        return None

    category, item_type = get_category(tags)
    environment = classify_environment(tags, category)

    # Build description from available tags
    desc_parts = []
    if tags.get("description"):
        desc_parts.append(tags["description"][:300])
    elif tags.get("description:vi"):
        desc_parts.append(tags["description:vi"][:300])
    if tags.get("cuisine"):
        desc_parts.append(f"Ẩm thực: {tags['cuisine'].replace(';', ', ')}")
    if tags.get("website") or tags.get("contact:website"):
        pass  # just keep it in source_url

    # Address
    addr_parts = [
        tags.get("addr:housenumber", ""),
        tags.get("addr:street", ""),
        tags.get("addr:suburb", ""),
        tags.get("addr:quarter", ""),
    ]
    address = ", ".join(p for p in addr_parts if p).strip(", ")
    district = (
        tags.get("addr:suburb")
        or tags.get("addr:quarter")
        or tags.get("addr:district")
        or ""
    )

    # Source URL
    source = (
        tags.get("website")
        or tags.get("contact:website")
        or tags.get("url")
        or f"https://www.openstreetmap.org/node/{elem.get('id', '')}"
    )

    # Phone
    phone = tags.get("phone") or tags.get("contact:phone") or ""

    # Ticket / price
    ticket_price = parse_price(tags)

    # Operating hours
    hours = parse_hours(tags)

    # Parking
    car_parking = tags.get("parking") in ("yes", "public", "private") or tags.get("car_parking") == "yes"

    # Rating (OSM doesn't have ratings, use 0)
    rating = 0.0

    record = {
        "name": name,
        "category": category,
        "type": item_type,
        "environment": environment,
        "district": district,
        "city": city_info["city"],
        "lat": round(lat, 7),
        "lng": round(lng, 7),
        "description": " ".join(desc_parts)[:400],
        "address": address,
        "phone": phone,
        "operating_hours": hours,
        "estimated_cost": ticket_price,
        "estimated_rating": rating,
        "car_parking": car_parking,
        "motorbike_parking": True,
        "meal_time": infer_meal_time(tags) if item_type == "food" else [],
        "season_tags": ["all_year"],
        "style": "heritage" if category in ("heritage", "spiritual", "culture") else "trending",
        "source_url": source,
        "osm_id": elem.get("id"),
        "osm_tags": {k: v for k, v in tags.items() if k in (
            "amenity", "tourism", "historic", "natural", "cuisine",
            "opening_hours", "phone", "website", "addr:street", "fee"
        )},
    }
    return record


# ── Query groups per city ──────────────────────────────────────────────────────
QUERY_GROUPS = [
    # Food & drinks
    (["amenity=restaurant", "amenity=fast_food"], "Restaurants"),
    (["amenity=cafe"], "Cafes"),
    (["amenity=bar", "amenity=pub"], "Bars/Pubs"),
    (["amenity=food_court", "amenity=ice_cream"], "Street Food"),
    # Sightseeing
    (["tourism=attraction", "tourism=viewpoint"], "Attractions"),
    (["tourism=museum", "tourism=gallery"], "Museums"),
    (["historic=monument", "historic=memorial", "historic=ruins",
      "historic=archaeological_site", "historic=castle", "historic=fort"], "Historic"),
    (["amenity=place_of_worship"], "Spiritual"),
    # Nature
    (["natural=beach", "leisure=beach_resort"], "Beaches"),
    (["natural=peak", "natural=cave_entrance", "natural=waterfall"], "Nature"),
    # Entertainment
    (["tourism=theme_park", "leisure=park", "leisure=garden"], "Parks"),
    (["amenity=theatre", "amenity=cinema", "amenity=arts_centre"], "Entertainment"),
    # Shopping
    (["amenity=marketplace", "shop=mall", "shop=supermarket"], "Shopping"),
]


def crawl_city(city_name: str, city_info: dict, limit_per_group: int = 300) -> list:
    """Crawl all location types for one city."""
    all_records = []
    seen_ids = set()
    seen_names = set()

    print(f"\n  City: {city_name.encode('ascii','replace').decode()} (bbox {city_info['bbox']})")

    for filters, group_name in QUERY_GROUPS:
        query = build_query(city_info["bbox"], filters, limit=limit_per_group)
        elements = overpass_query(query)
        group_records = []

        for elem in elements:
            osm_id = elem.get("id")
            if osm_id in seen_ids:
                continue

            record = element_to_record(elem, city_info)
            if not record:
                continue

            # Deduplicate by name (case-insensitive)
            name_key = record["name"].lower().strip()
            if name_key in seen_names:
                continue

            seen_ids.add(osm_id)
            seen_names.add(name_key)
            group_records.append(record)

        all_records.extend(group_records)
        print(f"    [{group_name}]: {len(elements)} OSM elements -> {len(group_records)} valid records")
        time.sleep(REQUEST_DELAY)

    return all_records


def main():
    parser = argparse.ArgumentParser(description="Crawl OSM data for LUNA tourism AI")
    parser.add_argument("--city", help="Only crawl this city (e.g. 'Đà Nẵng'). Default: all")
    parser.add_argument("--limit", type=int, default=300, help="Max results per query group per city")
    parser.add_argument("--out", default=str(OUTPUT_FILE), help="Output JSON file path")
    args = parser.parse_args()

    cities_to_crawl = CITIES
    if args.city:
        search = args.city.lower()
        alias = {"danang": "Đà Nẵng", "da nang": "Đà Nẵng", "nang": "Đà Nẵng",
                 "hoian": "Hội An", "hoi an": "Hội An", "hue": "Huế", "hué": "Huế"}
        resolved = alias.get(search, args.city)
        cities_to_crawl = {k: v for k, v in CITIES.items() if resolved.lower() in k.lower() or k.lower() in resolved.lower()}
        if not cities_to_crawl:
            print(f"Unknown city. Valid: Da Nang, Hoi An, Hue")
            sys.exit(1)

    all_records = []

    for city_name, city_info in cities_to_crawl.items():
        records = crawl_city(city_name, city_info, limit_per_group=args.limit)
        all_records.extend(records)

    # Summary
    by_city = {}
    by_type = {}
    for r in all_records:
        by_city[r["city"]] = by_city.get(r["city"], 0) + 1
        by_type[r["type"]] = by_type.get(r["type"], 0) + 1

    print(f"\n=== Crawl Summary ===")
    print(f"Total records: {len(all_records)}")
    for city, count in sorted(by_city.items(), key=lambda x: -x[1]):
        print(f"  {city.encode('ascii','replace').decode()}: {count}")
    print(f"  Locations: {by_type.get('location', 0)}, Food: {by_type.get('food', 0)}")

    # Save
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_records, f, ensure_ascii=False, indent=2)
    print(f"\nSaved -> {out_path}")

    # Also write directly to ai-engine seed files for immediate use
    seed_dir = Path(__file__).parent.parent / "ai-engine" / "data" / "seed"
    seed_dir.mkdir(parents=True, exist_ok=True)

    locations = [r for r in all_records if r["type"] == "location"]
    food = [r for r in all_records if r["type"] == "food"]

    with open(seed_dir / "locations.json", "w", encoding="utf-8") as f:
        json.dump(locations, f, ensure_ascii=False, indent=2)
    with open(seed_dir / "food.json", "w", encoding="utf-8") as f:
        json.dump(food, f, ensure_ascii=False, indent=2)

    print(f"Seed files updated: {len(locations)} locations, {len(food)} food")
    print("Next: docker compose build ai-engine && docker compose up -d ai-engine")


if __name__ == "__main__":
    main()
