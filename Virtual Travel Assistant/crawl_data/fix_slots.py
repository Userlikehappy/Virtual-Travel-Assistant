from datetime import datetime
def test_slots():
    slots = [
        {"name": "Điểm tâm", "start": "07:00", "end": "08:30", "type": "breakfast"},
        {"name": "Tham quan sáng", "start": "09:00", "end": "11:30", "type": "sightseeing"},
    ]
    for s in slots:
        start_time = datetime.strptime(s["start"], "%H:%M")
        end_time = datetime.strptime(s["end"], "%H:%M")
        diff = end_time - start_time
        print(f"{s['name']}: {diff.total_seconds() / 60} mins")
test_slots()
