'use client';

import { useState, useEffect } from 'react';
import styles from './page.module.css';

export default function WeatherPage() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(null);

  const fetchData = async () => {
    try {
      // Force cache bypass
      const res = await fetch('/data.json?t=' + new Date().getTime());
      if (!res.ok) {
        throw new Error('데이터를 불러오지 못했습니다.');
      }
      const jsonData = await res.json();
      setData(jsonData);
      setError(null);
    } catch (err) {
      console.error(err);
      setError('날씨 데이터를 가져오는 중 오류가 발생했습니다.');
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
      const res = await fetch('/api/trigger', { method: 'POST' });
      if (!res.ok) throw new Error('업데이트 요청 실패');
      alert('데이터 최신화 요청을 GitHub Actions에 전달했습니다. 약 1~2분 후 갱신됩니다.');
    } catch (err) {
      console.error(err);
      alert('업데이트 요청 중 오류가 발생했습니다.');
    } finally {
      setRefreshing(false);
    }
  };

  const formatTime = (timeStr) => {
    return `${timeStr.substring(0, 2)}:00`;
  };

  if (loading) {
    return (
      <main className={styles.container}>
        <div className={styles.message}>날씨 정보를 불러오는 중입니다...</div>
      </main>
    );
  }

  return (
    <main className={styles.container}>
      <div className={styles.header}>
        <h1 className={styles.title}>양평군 날씨 현황</h1>
        <p className={styles.subtitle}>
          마지막 업데이트: {data ? new Date(data.updatedAt).toLocaleString('ko-KR') : '-'}
        </p>
      </div>

      <div className={styles.glassCard}>
        <div className={styles.accumulatedSection}>
          <div>
            <div className={styles.accumulatedLabel}>오늘 누적 강수량</div>
            <div className={styles.accumulatedValue}>
              {data ? `${data.accumulated} mm` : '0 mm'}
            </div>
          </div>
          <button 
            className={styles.refreshButton} 
            onClick={handleRefresh}
            disabled={refreshing}
          >
            <svg 
              className={refreshing ? styles.spin : ''} 
              width="20" height="20" viewBox="0 0 24 24" 
              fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
            >
              <path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.59-9.21l5.67-5.67"/>
            </svg>
            {refreshing ? '요청 중...' : '최신화하기'}
          </button>
        </div>
      </div>

      <div className={styles.glassCard}>
        <h2 className={styles.expectedTitle}>시간대별 예상 강수량 (내일 09시까지)</h2>
        {error ? (
          <div style={{ color: '#ffb3b3' }}>{error}</div>
        ) : (
          <div className={styles.timeline}>
            {data && data.expected && data.expected.length > 0 ? (
              data.expected.map((item, idx) => (
                <div key={idx} className={styles.timelineItem}>
                  <div className={styles.time}>{formatTime(item.time)}</div>
                  <div className={styles.value}>{item.value}</div>
                </div>
              ))
            ) : (
              <div style={{ color: 'var(--text-secondary)' }}>예상 강수량 데이터가 없습니다.</div>
            )}
          </div>
        )}
      </div>
    </main>
  );
}
