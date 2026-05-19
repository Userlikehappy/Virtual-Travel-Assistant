const xlsx = require('xlsx');
const path = require('path');

const workbook = xlsx.readFile(path.join(__dirname, '../thong_tin_danh_thang_dia_diem_du_lich_tai_thanh_ph.xlsx'));
console.log(workbook.SheetNames);
for (const name of workbook.SheetNames) {
    const sheet = workbook.Sheets[name];
    const data = xlsx.utils.sheet_to_json(sheet, {header: 1});
    console.log(`Sheet: ${name}, Headers: ${JSON.stringify(data[0])}`);
}
