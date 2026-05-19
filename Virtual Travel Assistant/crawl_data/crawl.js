const xlsx = require('xlsx');
const fs = require('fs');
const path = require('path');
const axios = require('axios');
const cheerio = require('cheerio');

// Constants
const EXCEL_PATH = path.join(__dirname, '../thong_tin_danh_thang_dia_diem_du_lich_tai_thanh_ph.xlsx');
const OUTPUT_DIR = path.join(__dirname, 'output');

// Config
const CRAWL_DELAY = 1500; 
const MAX_ITEMS_TO_CRAWL = parseInt(process.argv[2]) || 0; 

if (!fs.existsSync(OUTPUT_DIR)) {
    fs.mkdirSync(OUTPUT_DIR, { recursive: true });
}

const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));

// Crawler: DuckDuckGo Search (HTML fallback) - Phân tích nhiều nguồn
async function searchWeb(query) {
    try {
        const url = `https://html.duckduckgo.com/html/?q=${encodeURIComponent(query)}`;
        const response = await axios.get(url, {
            headers: { 
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept-Language': 'vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7'
            },
            timeout: 5000
        });
        
        const $ = cheerio.load(response.data);
        
        const firstResult = $('.result__snippet').first().text().trim();
        const firstUrl = $('.result__url').first().attr('href');
        
        // Trích xuất các platform khác nhau từ top kết quả tìm kiếm
        let tripAdvisorUrl = null;
        let facebookUrl = null;
        let foodyUrl = null;
        let tiktokUrl = null;
        let youtubeUrl = null;

        $('.result__url').each((i, el) => {
            let link = $(el).attr('href');
            if (!link) return;
            
            // Xử lý link redirect của DuckDuckGo nếu có
            if (link.startsWith('//duckduckgo.com/l/?uddg=')) {
                try { link = decodeURIComponent(link.split('uddg=')[1].split('&')[0]); } catch(e){}
            }

            if (!tripAdvisorUrl && link.includes('tripadvisor.com')) tripAdvisorUrl = link;
            if (!facebookUrl && link.includes('facebook.com')) facebookUrl = link;
            if (!foodyUrl && (link.includes('foody.vn') || link.includes('shopeefood.vn'))) foodyUrl = link;
            if (!tiktokUrl && link.includes('tiktok.com')) tiktokUrl = link;
            if (!youtubeUrl && link.includes('youtube.com/watch')) youtubeUrl = link;
        });

        const reviewsText = $('.result__snippet').text();
        const ratingMatch = reviewsText.match(/(\d[\.,]\d)\s?\/\s?5/);
        const rating = ratingMatch ? parseFloat(ratingMatch[1].replace(',', '.')) : null;

        return { 
            snippet: firstResult || null,
            source_url: firstUrl || null,
            tripadvisor_url: tripAdvisorUrl,
            facebook_url: facebookUrl,
            foody_url: foodyUrl,
            tiktok_url: tiktokUrl,
            youtube_url: youtubeUrl,
            estimated_rating: rating
        };
    } catch (e) {
        return { snippet: null };
    }
}

// Crawler: Wikipedia API
async function searchWikipedia(query) {
    try {
        const url = `https://vi.wikipedia.org/w/api.php?action=query&list=search&srsearch=${encodeURIComponent(query)}&utf8=&format=json`;
        const res = await axios.get(url, { timeout: 5000 });
        const results = res.data.query.search;
        if (results && results.length > 0) {
            const title = results[0].title;
            const snippet = results[0].snippet.replace(/(<([^>]+)>)/gi, "");
            
            const pageUrl = `https://vi.wikipedia.org/w/api.php?action=query&titles=${encodeURIComponent(title)}&prop=pageimages&format=json&pithumbsize=500`;
            const pageRes = await axios.get(pageUrl, { timeout: 5000 });
            const pages = pageRes.data.query.pages;
            const pageId = Object.keys(pages)[0];
            const imageUrl = pages[pageId].thumbnail ? pages[pageId].thumbnail.source : null;

            return { 
                snippet: snippet,
                source_url: `https://vi.wikipedia.org/wiki/${encodeURIComponent(title)}`,
                image_url: imageUrl
            };
        }
    } catch (e) { }
    return null;
}

// Master Crawl Function
async function enrichData(name, type, city) {
    const query = `${name} ${city || ''} ${type === 'food' ? 'foody review' : 'du lịch review'}`;
    let result = {
        crawled_snippet: null,
        source_url: null,
        image_url: null,
        tripadvisor_url: null,
        facebook_url: null,
        foody_url: null,
        tiktok_url: null,
        youtube_url: null,
        estimated_rating: null
    };

    if (type !== 'food') {
        const wiki = await searchWikipedia(name);
        if (wiki && wiki.snippet) {
            result.crawled_snippet = wiki.snippet;
            result.source_url = wiki.source_url;
            result.image_url = wiki.image_url;
        }
    }
    
    // Luôn quét web để lấy thêm các link MXH (FB, Tiktok, Foody)
    const web = await searchWeb(query);
    if (web) {
        if (!result.crawled_snippet) result.crawled_snippet = web.snippet;
        if (!result.source_url) result.source_url = web.source_url;
        result.tripadvisor_url = web.tripadvisor_url;
        result.facebook_url = web.facebook_url;
        result.foody_url = web.foody_url;
        result.tiktok_url = web.tiktok_url;
        result.youtube_url = web.youtube_url;
        result.estimated_rating = web.estimated_rating;
    }
    
    if (!result.crawled_snippet) {
        result.crawled_snippet = 'Không tìm thấy thông tin chi tiết trên Internet.';
    }

    return result;
}

// Parsers
function parseSightseeingSheet1(sheet) {
    const data = xlsx.utils.sheet_to_json(sheet);
    return data.map(row => ({
        name: row['Tên chính thức'] || '',
        address: row['Địa chỉ chi tiết'] || '',
        district: row['Quận/Huyện'] || '',
        city: row['Thành phố'] || '',
        gps: row['GPS'] || '',
        category: row['Loại hình trải nghiệm'] || 'sightseeing',
        environment: row['Trong nhà/Ngoài trời'] || 'Outdoor',
        operating_hours: row['Giờ mở cửa'] || '',
        best_time: row['Thời điểm đẹp nhất (Day)'] || '',
        best_season: row['Mùa đẹp nhất (Season)'] || '',
        type: 'sightseeing'
    })).filter(item => item.name);
}

function parseSightseeingSheet2(sheet) {
    const data = xlsx.utils.sheet_to_json(sheet);
    return data.map(row => ({
        id: row['ID'] || '',
        name: row['Tên'] || '',
        description: row['Mô tả'] || '',
        contact: row['Liên hệ'] || '',
        address: row['Địa chỉ chi tiết'] || '',
        district: row['Quận/Huyện'] || '',
        city: row['Thành phố'] || '',
        lat: row['Vĩ độ'] || '',
        lng: row['Kinh độ'] || '',
        operating_hours: row['Giờ mở cửa/đóng cửa'] || '',
        ticket_adult: row['Giá vé người lớn'] || '',
        type: 'sightseeing'
    })).filter(item => item.name);
}

function parseFoodSheet(sheet) {
    const data = xlsx.utils.sheet_to_json(sheet);
    return data.map(row => {
        // Cố gắng linh hoạt tìm các cột Vĩ độ / Kinh độ dù tên cột có thể hơi khác
        let lat = row['Vĩ độ'] || row['Lat'] || row['Latitude'] || row['lat'] || '';
        let lng = row['Kinh độ'] || row['Lng'] || row['Longitude'] || row['lng'] || '';
        
        return {
            name: row['Tên'] || row['Tên quán'] || row['Tên địa điểm'] || row['Tên quán ăn'] || '',
            description: row['Mô tả'] || row['Loại hình'] || row['Món ăn'] || '',
            address: row['Địa chỉ'] || row['Địa chỉ chi tiết'] || '',
            district: row['Quận'] || row['Quận/Huyện'] || '',
            city: row['Thành phố'] || row['Tỉnh/Thành phố'] || '',
            lat: lat,
            lng: lng,
            type: 'food'
        }
    }).filter(item => item.name);
}

// Main Process
async function main() {
    console.log(`[1] Đang đọc file Excel: ${EXCEL_PATH}`);
    const workbook = xlsx.readFile(EXCEL_PATH);
    
    let allLocations = [];
    if (workbook.Sheets['ĐỊA ĐIỂM DU LỊCH ']) allLocations = allLocations.concat(parseSightseeingSheet1(workbook.Sheets['ĐỊA ĐIỂM DU LỊCH ']));
    if (workbook.Sheets['Địa điểm ']) allLocations = allLocations.concat(parseSightseeingSheet2(workbook.Sheets['Địa điểm ']));
    if (workbook.Sheets['Ăn uống']) allLocations = allLocations.concat(parseFoodSheet(workbook.Sheets['Ăn uống']));
    
    const uniqueLocations = [];
    const seen = new Set();
    for (const loc of allLocations) {
        const key = loc.name.toLowerCase().trim();
        if (!seen.has(key)) {
            seen.add(key);
            uniqueLocations.push(loc);
        }
    }
    
    const limit = MAX_ITEMS_TO_CRAWL > 0 ? MAX_ITEMS_TO_CRAWL : uniqueLocations.length;
    console.log(`[2] Sẽ cào ${limit} địa điểm (Có FB, Foody, Tiktok, Youtube)...\n`);
    
    const results = [];
    
    for (let i = 0; i < limit; i++) {
        const item = uniqueLocations[i];
        process.stdout.write(`[${i+1}/${limit}] Cào dữ liệu: ${item.name} `);
        
        try {
            const enriched = await enrichData(item.name, item.type, item.city);
            
            // Build Google Maps Link
            let googleMapsUrl = '';
            if (item.lat && item.lng) {
                googleMapsUrl = `https://www.google.com/maps/search/?api=1&query=${item.lat},${item.lng}`;
            } else if (item.gps) {
                googleMapsUrl = `https://www.google.com/maps/search/?api=1&query=${item.gps.replace(' ', '')}`;
            } else {
                googleMapsUrl = `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(item.name + ' ' + (item.city || ''))}`;
            }
            
            // Logging Icons
            if (enriched.image_url) process.stdout.write('📸');
            if (enriched.facebook_url) process.stdout.write('📘');
            if (enriched.foody_url) process.stdout.write('🍲');
            if (enriched.tiktok_url || enriched.youtube_url) process.stdout.write('🎵');
            if (enriched.tripadvisor_url) process.stdout.write('🦉');
            process.stdout.write('🗺️ \n');
            
            results.push({
                ...item,
                crawled_snippet: enriched.crawled_snippet,
                image_url: enriched.image_url,
                source_url: enriched.source_url,
                google_maps_url: googleMapsUrl,
                facebook_url: enriched.facebook_url,
                foody_url: enriched.foody_url,
                tiktok_url: enriched.tiktok_url,
                youtube_url: enriched.youtube_url,
                tripadvisor_url: enriched.tripadvisor_url,
                estimated_rating: enriched.estimated_rating,
                crawled_at: new Date().toISOString()
            });
        } catch (err) {
            console.log('❌ Lỗi');
        }
        
        if ((i + 1) % 10 === 0 || i === limit - 1) {
            const outputPath = path.join(OUTPUT_DIR, 'crawled_data_full.json');
            fs.writeFileSync(outputPath, JSON.stringify(results, null, 2), 'utf-8');
        }
        
        await sleep(CRAWL_DELAY);
    }
    console.log(`✅ Hoàn tất! Dữ liệu cuối cùng được lưu tại: ${path.join(OUTPUT_DIR, 'crawled_data_full.json')}`);
}

main().catch(err => console.error(err));
