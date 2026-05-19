import grpc
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), 'src', 'grpc_server'))
import luna_service_pb2 as luna_pb2
import luna_service_pb2_grpc as luna_pb2_grpc

def test_swap_location():
    print("🚀 Bắt đầu test gRPC SwapLocation (Python AI Engine)...")
    
    # Kết nối tới gRPC server đang chạy
    try:
        channel = grpc.insecure_channel('localhost:50051')
        stub = luna_pb2_grpc.AIEngineStub(channel)
        
        # Gửi request SwapLocation
        request = luna_pb2.SwapLocationRequest(
            user_id="test_user",
            itinerary_id="test_itinerary",
            place_name="Chợ Bến Thành",
            category="shopping",
            lat=10.7725,
            lng=106.6981,
            destination="Hồ Chí Minh"
        )
        
        print(f"📡 Đang gửi request đổi địa điểm: {request.place_name} ({request.category})...")
        response = stub.SwapLocation(request)
        
        print("✅ Thành công! Dữ liệu trả về từ gRPC:")
        print(f"  - Tên địa điểm mới: {response.new_slot.place_name}")
        print(f"  - Category: {response.new_slot.category}")
        print(f"  - Toạ độ: {response.new_slot.lat}, {response.new_slot.lng}")
        print(f"  - Môi trường: {response.new_slot.environment}")
        print(f"  - Ghi chú (Note): {response.new_slot.note}")
        print(f"  - Độ tương đồng (Score): {response.new_slot.score}")
        
    except grpc.RpcError as e:
        print(f"❌ Lỗi gRPC: {e.code()} - {e.details()}")
    except Exception as e:
        print(f"❌ Lỗi khác: {e}")

if __name__ == '__main__':
    test_swap_location()
