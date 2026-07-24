require('dotenv').config();
const fs = require('fs');
const path = require('path');
const https = require('https');

const API_KEY = process.env.DATA_GO_KR_KEY;
if (!API_KEY) {
  console.error("DATA_GO_KR_KEY is not set.");
  process.exit(1);
}

const REGIONS = [
  { name: "양평", nx: 69, ny: 125, sido: "경기", station: "양평읍" },
  { name: "경산", nx: 91, ny: 90, sido: "경북", station: "중방동" },
  { name: "사천", nx: 79, ny: 73, sido: "경남", station: "사천읍" },
  { name: "함안", nx: 86, ny: 77, sido: "경남", station: "가야읍" },
  { name: "성주", nx: 83, ny: 91, sido: "경북", station: "성주읍" },
  { name: "세종", nx: 66, ny: 103, sido: "세종", station: "신흥동" },
  { name: "계룡", nx: 65, ny: 99, sido: "충남", station: "두마면" },
  { name: "임실", nx: 66, ny: 84, sido: "전북", station: "임실읍" }
];

async function fetchWithRetry(url, retries = 3) {
  for (let i = 0; i < retries; i++) {
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const text = await res.text();
      try {
        return JSON.parse(text);
      } catch (e) {
        return null; // Return null on JSON parse error (e.g. XML error page)
      }
    } catch (err) {
      if (i === retries - 1) return null;
      await new Promise(r => setTimeout(r, 1000));
    }
  }
}

function getKstDate(d) {
  const kst = new Date(d.getTime() + 9 * 60 * 60 * 1000);
  return {
    yyyy: kst.getUTCFullYear(),
    mm: String(kst.getUTCMonth() + 1).padStart(2, '0'),
    dd: String(kst.getUTCDate()).padStart(2, '0'),
    hh: String(kst.getUTCHours()).padStart(2, '0'),
    min: String(kst.getUTCMinutes()).padStart(2, '0'),
    dateStr: `${kst.getUTCFullYear()}${String(kst.getUTCMonth() + 1).padStart(2, '0')}${String(kst.getUTCDate()).padStart(2, '0')}`
  };
}

async function fetchWeatherWarnings() {
  const url = `http://apis.data.go.kr/1360000/WthrWrnInfoService/getWthrWrnMsg?serviceKey=${encodeURIComponent(API_KEY)}&pageNo=1&numOfRows=10&dataType=JSON&stnId=108`;
  const data = await fetchWithRetry(url);
  let t6 = "";
  if (data?.response?.body?.items?.item?.length > 0) {
    t6 = data.response.body.items.item[0].t6 || "";
  }
  return t6;
}

function getWarningForRegion(t6, regionName) {
  if (!t6) return "";
  const lines = t6.split('\n');
  const warnings = [];
  for (const line of lines) {
    if (line.includes(regionName)) {
      const match = line.match(/o\s+([^:]+):/);
      if (match) {
        warnings.push(match[1].trim());
      }
    }
  }
  return warnings.length > 0 ? warnings.join('\n') : "";
}

async function fetchAirQuality() {
  const sidos = [...new Set(REGIONS.map(r => r.sido))];
  const airData = {};
  
  for (const sido of sidos) {
    const url = `http://apis.data.go.kr/B552584/ArpltnInforInqireSvc/getCtprvnRltmMesureDnsty?serviceKey=${encodeURIComponent(API_KEY)}&returnType=json&numOfRows=100&pageNo=1&sidoName=${encodeURIComponent(sido)}&ver=1.0`;
    const data = await fetchWithRetry(url);
    if (data?.response?.body?.items) {
      data.response.body.items.forEach(item => {
        airData[`${sido}_${item.stationName}`] = {
          pm10: item.pm10Value,
          pm25: item.pm25Value
        };
      });
    }
  }
  return airData;
}

function getAirQualityGrade(val, type) {
  if (!val || val === '-') return "";
  const v = parseInt(val);
  if (type === 'pm10') {
    if (v <= 30) return '좋음';
    if (v <= 80) return '보통';
    if (v <= 150) return '나쁨';
    return '매우나쁨';
  } else {
    if (v <= 15) return '좋음';
    if (v <= 35) return '보통';
    if (v <= 75) return '나쁨';
    return '매우나쁨';
  }
}

async function fetchWeatherDataForRegion(region, now) {
  const { yyyy, mm, dd, hh, min } = getKstDate(now);
  const dateStr = `${yyyy}${mm}${dd}`;
  
  let currentHour = parseInt(hh);
  if (parseInt(min) < 40) currentHour -= 1;
  if (currentHour < 0) currentHour = 0; // midnight edge case

  let totalPrecipitation = 0;
  let latestData = {};
  
  for (let i = 0; i <= currentHour; i++) {
    const base_time = String(i).padStart(2, '0') + "00";
    const url = `http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getUltraSrtNcst?serviceKey=${encodeURIComponent(API_KEY)}&pageNo=1&numOfRows=100&dataType=JSON&base_date=${dateStr}&base_time=${base_time}&nx=${region.nx}&ny=${region.ny}`;
    
    const data = await fetchWithRetry(url);
    const items = data?.response?.body?.items?.item;
    if (items) {
      items.forEach(item => {
        if (i === currentHour) {
          if (item.category === 'T1H') latestData.temp = item.obsrValue + '℃';
          if (item.category === 'WSD') latestData.wsd = item.obsrValue;
          if (item.category === 'VEC') latestData.vec = item.obsrValue;
          if (item.category === 'PTY') latestData.pty = item.obsrValue;
        }
        if (item.category === 'RN1') {
          const obs = item.obsrValue;
          if (obs !== '강수없음' && !isNaN(parseFloat(obs))) {
            totalPrecipitation += parseFloat(obs);
          }
        }
      });
    }
  }

  let windDir = '바람';
  if (latestData.vec) {
    const v = parseInt(latestData.vec);
    const dirs = ['북풍', '북동풍', '동풍', '남동풍', '남풍', '남서풍', '서풍', '북서풍', '북풍'];
    windDir = dirs[Math.round(v / 45)];
  }
  const windStr = latestData.wsd ? `${windDir}\n${latestData.wsd}m/s` : '';

  let skyStr = '맑음';
  if (latestData.pty && latestData.pty !== '0') {
    if (latestData.pty === '1' || latestData.pty === '4') skyStr = '비';
    else if (latestData.pty === '2') skyStr = '비/눈';
    else if (latestData.pty === '3') skyStr = '눈';
  } else {
    skyStr = '구름많음';
  }

  // VilageFcst
  let vDateStr = dateStr;
  const times = [2, 5, 8, 11, 14, 17, 20, 23];
  let baseHour = -1;
  for (let i = times.length - 1; i >= 0; i--) {
    const t = times[i];
    if (parseInt(hh) > t || (parseInt(hh) === t && parseInt(min) >= 10)) {
      baseHour = t;
      break;
    }
  }
  if (baseHour === -1) {
    const yesterday = new Date(now.getTime() - 24 * 60 * 60 * 1000);
    vDateStr = getKstDate(yesterday).dateStr;
    baseHour = 23;
  }
  const base_time_v = String(baseHour).padStart(2, '0') + "00";
  
  const vUrl = `http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst?serviceKey=${encodeURIComponent(API_KEY)}&pageNo=1&numOfRows=1000&dataType=JSON&base_date=${vDateStr}&base_time=${base_time_v}&nx=${region.nx}&ny=${region.ny}`;
  const vData = await fetchWithRetry(vUrl);
  const vItems = vData?.response?.body?.items?.item || [];
  
  const pcpItems = vItems.filter(item => item.category === 'PCP');
  const skyItems = vItems.filter(item => item.category === 'SKY');
  
  const todayStr = dateStr;
  const tomorrowStr = getKstDate(new Date(now.getTime() + 24 * 60 * 60 * 1000)).dateStr;
  
  const expected = [];
  for (const item of pcpItems) {
    const fcstDate = item.fcstDate;
    const fcstHour = parseInt(item.fcstTime.substring(0, 2));
    let include = false;
    if (fcstDate === todayStr && fcstHour > currentHour) include = true;
    else if (fcstDate === tomorrowStr && fcstHour <= 9) include = true;
    
    if (include) {
      let val = item.fcstValue;
      if (val === '강수없음') val = '0mm';
      expected.push({ date: fcstDate, time: item.fcstTime, value: val });
    }
  }

  if (skyStr === '구름많음') {
    const currentSkyItem = skyItems.find(item => item.fcstDate === todayStr && parseInt(item.fcstTime.substring(0,2)) === currentHour);
    if (currentSkyItem) {
      if (currentSkyItem.fcstValue === '1') skyStr = '맑음';
      if (currentSkyItem.fcstValue === '3') skyStr = '구름많음';
      if (currentSkyItem.fcstValue === '4') skyStr = '흐림';
    }
  }

  return {
    accumulated: parseFloat(totalPrecipitation.toFixed(1)),
    temp: latestData.temp || '',
    wind: windStr,
    sky: skyStr,
    expected
  };
}

async function main() {
  const now = new Date();
  try {
    console.log("Fetching weather warnings...");
    const t6 = await fetchWeatherWarnings();
    
    console.log("Fetching air quality...");
    const airData = await fetchAirQuality();
    
    const results = {};
    for (const region of REGIONS) {
      console.log(`Processing ${region.name}...`);
      const weather = await fetchWeatherDataForRegion(region, now);
      
      const warning = getWarningForRegion(t6, region.name);
      
      const airKey = `${region.sido}_${region.station}`;
      const pm10Raw = airData[airKey]?.pm10;
      const pm25Raw = airData[airKey]?.pm25;
      const pm10 = pm10Raw ? getAirQualityGrade(pm10Raw, 'pm10') : '';
      const pm25 = pm25Raw ? getAirQualityGrade(pm25Raw, 'pm25') : '';
      
      results[region.name] = {
        sky: weather.sky,
        pm10,
        pm25,
        temp: weather.temp,
        wind: weather.wind,
        accumulated: weather.accumulated,
        expected: weather.expected,
        warning
      };
    }
    
    const output = {
      updatedAt: now.toISOString(),
      regions: results
    };
    
    const outPath = path.join(__dirname, '..', 'public', 'data.json');
    fs.writeFileSync(outPath, JSON.stringify(output, null, 2));
    console.log("Updated data.json successfully.");
    
  } catch (err) {
    console.error("Error updating weather data:", err);
    process.exit(1);
  }
}

main();
