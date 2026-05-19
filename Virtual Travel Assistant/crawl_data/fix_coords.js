const fs = require('fs');

const file = './output/crawled_data_full.json';
if (!fs.existsSync(file)) process.exit(0);

const data = JSON.parse(fs.readFileSync(file, 'utf-8'));
let fixedCount = 0;

data.forEach(item => {
    // Check if lat and lng are swapped
    if (item.lat && item.lng) {
        let lat = parseFloat(item.lat);
        let lng = parseFloat(item.lng);
        
        // In Vietnam, lat is ~8-23, lng is ~102-109
        // If lat > 100 and lng < 30, they are definitely swapped
        if (lat > 100 && lng < 30) {
            item.lat = lng.toString();
            item.lng = lat.toString();
            fixedCount++;
        }
    }
});

if (fixedCount > 0) {
    fs.writeFileSync(file, JSON.stringify(data, null, 2));
    console.log(`Fixed ${fixedCount} swapped coordinates.`);
} else {
    console.log("No coordinates needed fixing.");
}
