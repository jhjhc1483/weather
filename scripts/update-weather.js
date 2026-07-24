const fs = require('fs');
const path = require('path');

const API_KEY = process.env.DATA_GO_KR_KEY;
if (!API_KEY) {
  console.error("No API key found in DATA_GO_KR_KEY");
  process.exit(1);
}

const NX = 69;
const NY = 125;

// Helper to format date as YYYYMMDD and HHMM
function getKstDate(date) {
  const kst = new Date(date.getTime() + 9 * 60 * 60 * 1000);
  const yyyy = kst.getUTCFullYear();
  const mm = String(kst.getUTCMonth() + 1).padStart(2, '0');
  const dd = String(kst.getUTCDate()).padStart(2, '0');
  const hh = String(kst.getUTCHours()).padStart(2, '0');
  const min = String(kst.getUTCMinutes()).padStart(2, '0');
  return { yyyy, mm, dd, hh, min, dateStr: `${yyyy}${mm}${dd}` };
}

async function fetchWithRetry(url, retries = 3) {
  for (let i = 0; i < retries; i++) {
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      return data;
    } catch (err) {
      console.error(`Fetch failed (attempt ${i + 1}):`, err.message);
      if (i === retries - 1) throw err;
      await new Promise(r => setTimeout(r, 1000));
    }
  }
}

async function getAccumulatedPrecipitation(now) {
  const { yyyy, mm, dd, hh, min } = getKstDate(now);
  const dateStr = `${yyyy}${mm}${dd}`;
  
  let currentHour = parseInt(hh);
  // UltraSrtNcst is available at HH:40
  if (parseInt(min) < 40) {
    currentHour -= 1;
  }
  if (currentHour < 0) {
    return 0; // It's midnight, no accumulated precipitation yet for today
  }

  let totalPrecipitation = 0;
  console.log(`Fetching accumulated precipitation from 00:00 to ${String(currentHour).padStart(2, '0')}:00...`);
  
  // Fetch from 00 to currentHour
  for (let i = 0; i <= currentHour; i++) {
    const base_time = String(i).padStart(2, '0') + "00";
    const url = `http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getUltraSrtNcst?serviceKey=${encodeURIComponent(API_KEY)}&pageNo=1&numOfRows=100&dataType=JSON&base_date=${dateStr}&base_time=${base_time}&nx=${NX}&ny=${NY}`;
    
    try {
      const data = await fetchWithRetry(url);
      const items = data?.response?.body?.items?.item;
      if (items) {
        const rn1Item = items.find(item => item.category === 'RN1');
        if (rn1Item) {
          const obs = rn1Item.obsrValue;
          if (obs !== '강수없음' && !isNaN(parseFloat(obs))) {
             totalPrecipitation += parseFloat(obs);
          }
        }
      }
    } catch (err) {
      console.error(`Failed to fetch hour ${base_time}`);
    }
  }
  
  return parseFloat(totalPrecipitation.toFixed(1));
}

function getLatestVilageFcstBaseTime(now) {
  const { yyyy, mm, dd, hh, min } = getKstDate(now);
  let dateStr = `${yyyy}${mm}${dd}`;
  let hour = parseInt(hh);
  let mins = parseInt(min);
  
  // Available times: 02, 05, 08, 11, 14, 17, 20, 23 (provided 10 mins later)
  const times = [2, 5, 8, 11, 14, 17, 20, 23];
  let baseHour = -1;
  
  for (let i = times.length - 1; i >= 0; i--) {
    const t = times[i];
    if (hour > t || (hour === t && mins >= 10)) {
      baseHour = t;
      break;
    }
  }
  
  if (baseHour === -1) {
    // Need to use previous day's 23:00
    const yesterday = new Date(now.getTime() - 24 * 60 * 60 * 1000);
    const yKst = getKstDate(yesterday);
    dateStr = yKst.dateStr;
    baseHour = 23;
  }
  
  return { base_date: dateStr, base_time: String(baseHour).padStart(2, '0') + "00" };
}

async function getExpectedPrecipitation(now) {
  const { base_date, base_time } = getLatestVilageFcstBaseTime(now);
  console.log(`Fetching expected precipitation (VilageFcst) based on ${base_date} ${base_time}`);
  
  const url = `http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst?serviceKey=${encodeURIComponent(API_KEY)}&pageNo=1&numOfRows=1000&dataType=JSON&base_date=${base_date}&base_time=${base_time}&nx=${NX}&ny=${NY}`;
  
  const data = await fetchWithRetry(url);
  const items = data?.response?.body?.items?.item;
  
  if (!items) {
    console.error("No items found in VilageFcst", JSON.stringify(data));
    return [];
  }
  
  // Filter for PCP
  const pcpItems = items.filter(item => item.category === 'PCP');
  
  // We want from current time (or next hour) until 09:00 of the next day
  const kstNow = getKstDate(now);
  const todayStr = kstNow.dateStr;
  const tomorrow = new Date(now.getTime() + 24 * 60 * 60 * 1000);
  const kstTomorrow = getKstDate(tomorrow);
  const tomorrowStr = kstTomorrow.dateStr;
  
  let currentHour = parseInt(kstNow.hh);
  
  const expected = [];
  
  for (const item of pcpItems) {
    const fcstDate = item.fcstDate;
    const fcstTime = item.fcstTime;
    const fcstHour = parseInt(fcstTime.substring(0, 2));
    
    let include = false;
    if (fcstDate === todayStr && fcstHour > currentHour) {
      include = true;
    } else if (fcstDate === tomorrowStr && fcstHour <= 9) {
      include = true;
    }
    
    if (include) {
      let val = item.fcstValue;
      if (val === '강수없음') val = '0mm';
      
      expected.push({
        date: fcstDate,
        time: fcstTime,
        value: val
      });
    }
  }
  
  return expected;
}

async function main() {
  const now = new Date();
  
  try {
    const accumulated = await getAccumulatedPrecipitation(now);
    const expected = await getExpectedPrecipitation(now);
    
    const output = {
      updatedAt: now.toISOString(),
      accumulated: accumulated,
      expected: expected
    };
    
    const outPath = path.join(__dirname, '..', 'public', 'data.json');
    fs.writeFileSync(outPath, JSON.stringify(output, null, 2));
    console.log(`Weather data successfully written to ${outPath}`);
    
  } catch (err) {
    console.error("Error updating weather data:", err);
    process.exit(1);
  }
}

main();
