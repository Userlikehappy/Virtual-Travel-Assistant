"""
LUNA AI Engine - Agent Skills (Function Calling)
Các hàm (tools) này sẽ được cấp cho Gemini để mô hình tự động quyết định lúc nào cần gọi.
"""

import logging
from src.utils.geo import haversine_distance

logger = logging.getLogger(__name__)

# Lưu ý: Các hàm cung cấp cho LLM phải có type hint rõ ràng và docstring chi tiết.
# SDK google-genai sẽ tự động parse docstring này thành FunctionDeclaration.

def calculate_distance(lat1: float, lng1: float, lat2: float, lng2: float) -> str:
    """
    Tính khoảng cách đường chim bay (khoảng cách thực tế) giữa 2 tọa độ địa lý.
    
    Args:
        lat1: Vĩ độ của điểm thứ nhất.
        lng1: Kinh độ của điểm thứ nhất.
        lat2: Vĩ độ của điểm thứ hai.
        lng2: Kinh độ của điểm thứ hai.
        
    Returns:
        Khoảng cách tính bằng Kilometer (km) dưới dạng chuỗi, ví dụ "5.2 km".
    """
    try:
        dist = haversine_distance(lat1, lng1, lat2, lng2)
        return f"{dist:.2f} km"
    except Exception as e:
        logger.error(f"Error in calculate_distance: {e}")
        return "Không thể tính toán khoảng cách."

def check_opening_hours(place_name: str, target_time: str, operating_hours: str) -> str:
    """
    Kiểm tra xem một địa điểm có đang mở cửa vào khung giờ dự kiến hay không.
    
    Args:
        place_name: Tên địa điểm.
        target_time: Giờ bạn định đến (định dạng HH:MM, ví dụ '08:30').
        operating_hours: Giờ mở cửa của quán lấy từ database (ví dụ '07:00 - 22:00').
        
    Returns:
        Chuỗi thông báo "MỞ CỬA" hoặc "ĐÓNG CỬA".
    """
    from src.services.itinerary_logic import is_open_during_slot
    try:
        # Trick: Mượn hàm is_open_during_slot, target_time được coi là start và end sát nhau
        is_open = is_open_during_slot(operating_hours, target_time, target_time)
        return f"{place_name} SẼ MỞ CỬA lúc {target_time}" if is_open else f"{place_name} SẼ ĐÓNG CỬA lúc {target_time}. Không nên gợi ý."
    except Exception as e:
        return "Không có dữ liệu rõ ràng, có thể mở cửa tự do."

def get_current_weather(location: str) -> str:
    """
    Tra cứu thông tin thời tiết hiện tại tại một tỉnh/thành phố.
    Dùng khi người dùng hỏi "thời tiết ở Đà Nẵng hôm nay thế nào?"
    
    Args:
        location: Tên thành phố (ví dụ: 'Đà Nẵng', 'Huế')
    """
    # Todo: Connect to real weather API. Dummy for now to prove tool calling works.
    if "đà nẵng" in location.lower():
        return "Trời nắng đẹp, 28 độ C, rất thích hợp đi biển."
    elif "huế" in location.lower():
        return "Trời có mưa rào nhẹ, 24 độ C, nên gợi ý các địa điểm trong nhà hoặc uống cafe."
    return "Trời mát mẻ, 26 độ C."
