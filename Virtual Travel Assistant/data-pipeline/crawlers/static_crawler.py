"""
LUNA Data Pipeline - Heritage & Static Data Crawler
Reads processed data from the JS crawler output and maps it to Neo4j schemas.
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

def crawl_static_data() -> list:
    """
    Load data from the output of the Node.js crawler (crawled_data_full.json).
    Maps raw crawled fields into the normalized schema for Neo4j and Milvus.
    """
    logger.info("📥 Loading raw crawled data from Node.js crawler output...")
    
    file_path = Path(__file__).parent.parent.parent / "crawl_data" / "output" / "crawled_data_full.json"
    if not file_path.exists():
        logger.error(f"❌ File not found: {file_path}")
        return []
        
    with open(file_path, "r", encoding="utf-8") as f:
        raw_data = json.load(f)
        
    normalized_data = []
    
    for item in raw_data:
        # Extract lat/lng
        lat, lng = 0.0, 0.0
        
        # 1. Check gps field
        if item.get("gps"):
            parts = str(item["gps"]).split(",")
            if len(parts) == 2:
                try:
                    lat, lng = float(parts[0].strip()), float(parts[1].strip())
                except:
                    pass
                    
        # 2. Check explicit lat/lng
        if lat == 0.0 and (item.get("lat") or item.get("lng")):
            try:
                lat = float(item.get("lat", 0.0) or 0.0)
                lng = float(item.get("lng", 0.0) or 0.0)
            except:
                pass
                
        # BỘ LỌC CHỐNG LỖI TỌA ĐỘ BỊ NGƯỢC (RẤT THAY GẶP TRONG EXCEL VIỆT NAM)
        # VN nằm ở vĩ độ (lat) ~ 8-23, kinh độ (lng) ~ 102-109
        if lat > 100 and lng < 30:
            lat, lng = lng, lat  # Swap
            
        # Loại bỏ hoàn toàn các địa điểm không có tọa độ hoặc tọa độ không hợp lệ
        if lat == 0.0 or lng == 0.0 or lat < 8 or lat > 24 or lng < 102 or lng > 110:
            continue
            
        # 3. Phân loại
        item_type = item.get("type", "sightseeing")
        category = "food" if item_type == "food" else item.get("category", "sightseeing")
        
        # 4. Map vào Schema
        norm = {
            "name": item.get("name", "Unknown"),
            "category": category.lower() if category else "sightseeing",
            "type": "location" if item_type != "food" else "food",
            "environment": item.get("environment", "Outdoor").capitalize() if item.get("environment") else "Outdoor",
            "district": item.get("district", ""),
            "city": item.get("city", ""),
            "lat": lat,
            "lng": lng,
            "description": item.get("crawled_snippet", "") or item.get("description", ""),
            "historical_significance": "",
            "operating_hours": item.get("operating_hours", ""),
            "ticket_price": {"adult": item.get("ticket_adult", "")},
            "car_parking": True if "food" not in category else False, # Giả định đơn giản
            "motorbike_parking": True,
            "visit_duration_minutes": 60,
            "style": "heritage", # Dữ liệu tĩnh luôn tính là heritage/truyền thống
            "best_time_of_day": ["morning", "afternoon"],
            "season_tags": ["all_year"],
            "meal_time": ["breakfast", "lunch", "dinner"] if item_type == "food" else [],
            "source_url": item.get("source_url", ""),
            "google_maps_url": item.get("google_maps_url", ""),
        }
        
        if norm["name"] and norm["name"] != "Unknown":
            normalized_data.append(norm)

    logger.info(f"✅ Loaded {len(normalized_data)} valid records from crawler output")
    return normalized_data


def save_raw_data(data: list, output_dir: str = "data/raw"):
    """Save raw crawled data to JSON files."""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    locations = [d for d in data if d.get("type") == "location"]
    food = [d for d in data if d.get("type") == "food"]

    with open(output_path / "locations.json", "w", encoding="utf-8") as f:
        json.dump(locations, f, ensure_ascii=False, indent=2)

    with open(output_path / "food.json", "w", encoding="utf-8") as f:
        json.dump(food, f, ensure_ascii=False, indent=2)

    logger.info(f"💾 Saved {len(locations)} locations and {len(food)} food items to {output_dir}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    data = crawl_static_data()
    save_raw_data(data)
