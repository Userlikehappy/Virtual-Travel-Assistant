import json
import logging
from pathlib import Path
import sys
import os
import re

current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(current_dir))
ai_engine_path = os.path.join(project_root, 'ai-engine')
sys.path.append(ai_engine_path)

from src.clients.neo4j_client import Neo4jClient
from src.clients.milvus_client import MilvusClient
from src.utils.embedding import embed_text

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

def parse_cost(val):
    if not val:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    
    matches = re.findall(r'\d+(?:[\.,]\d+)*', str(val))
    if matches:
        try:
            num_str = matches[0].replace('.', '').replace(',', '')
            return float(num_str)
        except:
            return 0.0
    return 0.0

def import_to_db(json_path: str):
    logger.info(f"Đang đọc dữ liệu từ: {json_path}")
    
    if not os.path.exists(json_path):
        logger.error(f"❌ Không tìm thấy file: {json_path}")
        return

    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    logger.info(f"📦 Đã tải {len(data)} bản ghi. Đang kết nối Database...")

    neo4j = Neo4jClient()
    milvus = MilvusClient()
    
    try:
        neo4j.connect()
        milvus.connect()
    except Exception as e:
        logger.error(f"❌ Lỗi kết nối DB: {e}")
        return

    logger.info("🚀 Bắt đầu nhập dữ liệu vào Neo4j (Knowledge Graph)...")
    
    success_count = 0
    
    for i, item in enumerate(data):
        name = item.get("name", "").strip()
        if not name: continue
        
        category = item.get("category", "sightseeing").lower()
        if item.get("type") == "food":
            category = "food"
            
        location_data = {
            "name": name,
            "category": category,
            "environment": item.get("environment", "Outdoor"),
            "district": item.get("district", ""),
            "city": item.get("city", "Đà Nẵng"),
            "lat": float(item.get("lat") or 0.0),
            "lng": float(item.get("lng") or 0.0),
            "cost": parse_cost(item.get("ticket_adult") or item.get("estimated_cost")),
            "description": item.get("crawled_snippet") or item.get("description", ""),
            "google_maps_url": item.get("google_maps_url", ""),
            "facebook_url": item.get("facebook_url", ""),
            "foody_url": item.get("foody_url", ""),
            "tiktok_url": item.get("tiktok_url", ""),
            "youtube_url": item.get("youtube_url", ""),
            "tripadvisor_url": item.get("tripadvisor_url", ""),
            "estimated_rating": float(item.get("estimated_rating") or 0.0),
        }
        
        gps = item.get("gps", "")
        if gps and not (location_data["lat"] and location_data["lng"]):
            try:
                parts = gps.replace(" ", "").split(",")
                if len(parts) == 2:
                    location_data["lat"] = float(parts[0])
                    location_data["lng"] = float(parts[1])
            except:
                pass
                
        try:
            query = """
            MERGE (l:Location {name: $name})
            SET l += $props
            """
            props = location_data.copy()
            del props["name"]
            
            neo4j.query(query, {"name": name, "props": props})
                
            success_count += 1
            if success_count % 50 == 0:
                logger.info(f"   Đã lưu {success_count}/{len(data)} vào Neo4j")
        except Exception as e:
            logger.warning(f"Lỗi khi lưu {name} vào Neo4j: {e}")

    logger.info(f"✅ Hoàn tất lưu {success_count} địa điểm vào Neo4j!")

    logger.info("🧠 Bắt đầu nhúng (Embedding) và lưu vào Milvus...")
    
    milvus_success = 0
    for i, item in enumerate(data):
        name = item.get("name", "").strip()
        if not name: continue
        
        desc = item.get("crawled_snippet") or item.get("description") or ""
        tags = item.get("category", "") + " " + item.get("district", "") + " " + item.get("city", "")
        
        text_to_embed = f"Tên: {name}. Địa điểm: {tags}. Mô tả: {desc}"
        
        try:
            vector = embed_text(text_to_embed)
            
            metadata = {
                "name": name,
                "category": item.get("type") == "food" and "food" or "sightseeing",
                "city": item.get("city", ""),
                "environment": item.get("environment", "Outdoor"),
                "cost": parse_cost(item.get("ticket_adult") or item.get("estimated_cost")),
                "lat": float(item.get("lat") or 0.0),
                "lng": float(item.get("lng") or 0.0)
            }
            
            if item.get("gps") and not (metadata["lat"] and metadata["lng"]):
                try:
                    parts = str(item.get("gps")).replace(" ", "").split(",")
                    if len(parts) == 2:
                        metadata["lat"] = float(parts[0])
                        metadata["lng"] = float(parts[1])
                except:
                    pass

            milvus.insert_vector(vector, metadata)
            milvus_success += 1
            
            if milvus_success % 50 == 0:
                logger.info(f"   Đã nhúng {milvus_success}/{len(data)} vào Milvus")
                
        except Exception as e:
            logger.warning(f"Lỗi khi lưu {name} vào Milvus: {e}")

    logger.info(f"✅ Hoàn tất lưu {milvus_success} địa điểm vào Milvus!")
    
    neo4j.close()
    milvus.close()
    logger.info("🎉 Toàn bộ quy trình nạp dữ liệu đã XONG!")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        json_file = sys.argv[1]
    else:
        json_file = os.path.join(project_root, "crawl_data", "output", "crawled_data_full.json")
        
    import_to_db(json_file)
