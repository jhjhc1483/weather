'use client';

import { useState, useEffect } from 'react';
import styles from './page.module.css';

const REGION_NAMES = ['양평', '경산', '사천', '함안', '성주', '세종', '계룡', '임실'];

export default function WeatherPage() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const fetchData = async () => {
    try {
      const res = await fetch('/data.json?t=' + new Date().getTime());
      if (res.ok) {
        const jsonData = await res.json();
        setData(jsonData);
      }
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      await fetch('/api/trigger', { method: 'POST' });
      alert('최신화 요청 완료. 잠시 후 새로고침해주세요.');
    } catch (err) {
      console.error(err);
    } finally {
      setRefreshing(false);
    }
  };

  const formatExpected = (expectedArr) => {
    if (!expectedArr || expectedArr.length === 0) return '';
    let ranges = [];
    let currentStart = null;
    let currentEnd = null;
    let totalVal = 0;

    for (let i = 0; i < expectedArr.length; i++) {
      const item = expectedArr[i];
      const valMatch = item.value.match(/([\d.]+)mm/);
      const isRainy = item.value !== '0mm' && item.value !== '강수없음';
      const valNum = valMatch ? parseFloat(valMatch[1]) : (isRainy ? 0.1 : 0);

      if (isRainy) {
        if (currentStart === null) {
          currentStart = item.time.substring(0, 2) + ":00";
        }
        currentEnd = String(parseInt(item.time.substring(0, 2)) + 1).padStart(2, '0') + ":00";
        totalVal += valNum;
      } else {
        if (currentStart !== null) {
          ranges.push(`${currentStart}~${currentEnd}(${Math.round(totalVal)}mm)`);
          currentStart = null;
          totalVal = 0;
        }
      }
    }
    if (currentStart !== null) {
      ranges.push(`${currentStart}~${currentEnd}(${Math.round(totalVal)}mm)`);
    }
    
    return ranges.join('\n');
  };

  const getDayOfWeek = () => {
    const days = ['일', '월', '화', '수', '목', '금', '토'];
    return days[new Date().getDay()];
  };

  const todayStr = `${new Date().getMonth() + 1}.${new Date().getDate()}`;
  const timeStr = `${new Date().getHours()}:00`;

  return (
    <main className={styles.container}>
      <div className={styles.titleContainer}>
        <div className={styles.title}>{todayStr} ({getDayOfWeek()}) 기상예보</div>
        <button className={styles.refreshButton} onClick={handleRefresh} disabled={refreshing}>
          {refreshing ? '갱신중...' : '최신화'}
        </button>
        <div className={styles.updateTime}>{timeStr}기준</div>
      </div>

      <table className={styles.weatherTable}>
        <thead>
          <tr>
            <th colSpan="2">구분</th>
            {REGION_NAMES.map(region => (
              <th key={region}>{region}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          <tr>
            <td colSpan="2">개황</td>
            {REGION_NAMES.map(region => (
              <td key={region}>{data?.regions?.[region]?.sky || ''}</td>
            ))}
          </tr>
          <tr>
            <td rowSpan="2" className={styles.labelCol}>미세먼지</td>
            <td className={styles.subLabelCol}>미세</td>
            {REGION_NAMES.map(region => (
              <td key={region}>{data?.regions?.[region]?.pm10 || ''}</td>
            ))}
          </tr>
          <tr>
            <td className={styles.subLabelCol}>초미세</td>
            {REGION_NAMES.map(region => (
              <td key={region}>{data?.regions?.[region]?.pm25 || ''}</td>
            ))}
          </tr>
          <tr>
            <td colSpan="2">기온</td>
            {REGION_NAMES.map(region => (
              <td key={region}>{data?.regions?.[region]?.temp || ''}</td>
            ))}
          </tr>
          <tr>
            <td colSpan="2">풍향/풍속</td>
            {REGION_NAMES.map(region => (
              <td key={region} style={{ whiteSpace: 'pre-line' }}>{data?.regions?.[region]?.wind || ''}</td>
            ))}
          </tr>
          <tr>
            <td colSpan="2">일일 누적 강수량</td>
            {REGION_NAMES.map(region => {
              const acc = data?.regions?.[region]?.accumulated;
              return <td key={region}>{acc !== undefined ? `${acc}mm` : ''}</td>;
            })}
          </tr>
          <tr>
            <td colSpan="2">일일 예상 강수량</td>
            {REGION_NAMES.map(region => (
              <td key={region} style={{ whiteSpace: 'pre-line' }}>{data?.regions?.[region] ? formatExpected(data.regions[region].expected) : ''}</td>
            ))}
          </tr>
          <tr>
            <td colSpan="2">기상특보</td>
            {REGION_NAMES.map(region => (
              <td key={region}>{data?.regions?.[region]?.warning || ''}</td>
            ))}
          </tr>
        </tbody>
      </table>
    </main>
  );
}
