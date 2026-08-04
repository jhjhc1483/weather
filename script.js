/**
 * 기상예보 대시보드 — 프론트엔드 스크립트 (완료 알림 및 스마트 폴링 지원)
 *
 * - data/weather.json fetch 및 동적 표출
 * - 다크 / 라이트 모드 전환 (localStorage)
 * - 갱신 버튼 클릭 시 백그라운드 트리거 및 갱신 완료 자동 감지 & 토스트 알림
 * - [업데이트] Vercel(.py) 및 Cloudflare(JS) 서버리스 환경 완벽 호환 지원
 */

(function () {
  'use strict';

  /* ======== 도메인 환경 감지 (Vercel / Cloudflare 호환) ======== */
  const isVercel = window.location.hostname.includes('vercel.app');

  /* ======== 상수 ======== */
  const DATA_URL = 'data/weather.json';
  
  // 접속 환경에 따라 백엔드 API 호출 주소를 동적으로 결정합니다.
  const TRIGGER_URL = isVercel ? '/api/trigger.py' : '/api/trigger';
  const CHECK_URL = isVercel ? '/api/check.py' : '/api/check';
  
  const AUTO_RELOAD_MS = 5 * 60 * 1000;  // 5분 자동 백그라운드 재로드
  const POLL_INTERVAL_MS = 20000;        // 갱신 요청 후 20초 간격 감지
  const POLL_TIMEOUT_MS = 5 * 60 * 1000; // 5분 후 감지 중단

  const LOCATION_ORDER = ['양평', '경산', '사천', '함안', '성주', '세종', '계룡', '임실'];

  /* ======== DOM ======== */
  const $body = document.getElementById('weather-body');
  const $mainTitle = document.getElementById('main-title');
  const $time = document.getElementById('time-display');
  const $apiBadge = document.getElementById('api-status-badge');
  const $btn = document.getElementById('refresh-btn');
  const $btnText = $btn ? $btn.querySelector('.btn-text') : null;
  const $toast = document.getElementById('toast');
  const $themeBtn = document.getElementById('theme-toggle-btn');

  /* ======== 상태 변수 ======== */
  const COOL_DOWN_MS = 30 * 60 * 1000; // 30분 쿨다운
  let currentUpdatedAt = '';
  let lastUpdatedAtTimestamp = 0;
  let currentApiStatus = null;
  let pollingStartUpdatedAt = '';
  let isPolling = false;
  let pollIntervalId = null;
  let pollTimeoutId = null;
  let audioCtx = null;

  /* ======== 버튼 쿨타임 및 진행 상태 관리 ======== */
  function updateButtonState() {
    if (!$btn) return;

    // 1. Action 실행 중 (스마트 폴링 중)
    if (isPolling) {
      $btn.disabled = true;
      $btn.classList.add('loading');
      $btn.classList.remove('cooldown');
      if ($btnText) $btnText.textContent = '갱신 중...';
      $btn.setAttribute('title', 'GitHub Actions 갱신 진행 중입니다 (약 2~3분 소요)');
      return;
    }

    // 2. 갱신 후 30분 쿨타임 검사
    const now = Date.now();
    let elapsed = 0;
    if (lastUpdatedAtTimestamp > 0) {
      elapsed = now - lastUpdatedAtTimestamp;
    }

    if (lastUpdatedAtTimestamp > 0 && elapsed < COOL_DOWN_MS) {
      const remainingMs = COOL_DOWN_MS - elapsed;
      const remainingMin = Math.max(1, Math.ceil(remainingMs / (60 * 1000)));

      // 쿨타임 종료 예상 시각
      const targetDate = new Date(lastUpdatedAtTimestamp + COOL_DOWN_MS);
      const hours = String(targetDate.getHours()).padStart(2, '0');
      const mins = String(targetDate.getMinutes()).padStart(2, '0');

      $btn.disabled = true;
      $btn.classList.remove('loading');
      $btn.classList.add('cooldown');
      if ($btnText) $btnText.textContent = `${remainingMin}분 후 가능`;
      $btn.setAttribute('title', `최신 데이터 유지 중 (30분 쿨타임). ${hours}:${mins} 이후 갱신 가능`);
    } else {
      // 3. 정상 상태 (갱신 가능)
      $btn.disabled = false;
      $btn.classList.remove('loading', 'cooldown');
      if ($btnText) $btnText.textContent = '지금 갱신';
      $btn.setAttribute('title', '지금 데이터 갱신 요청 (약 2~3분 소요)');
    }
  }

  /* ======== 테마 관리 ======== */
  function initTheme() {
    const savedTheme = localStorage.getItem('theme') || 'light';
    setTheme(savedTheme);
  }

  function setTheme(theme) {
    if (theme === 'light') {
      document.documentElement.setAttribute('data-theme', 'light');
      $themeBtn.setAttribute('title', '다크 모드로 전환');
    } else {
      document.documentElement.removeAttribute('data-theme');
      $themeBtn.setAttribute('title', '라이트 모드로 전환');
    }
    localStorage.setItem('theme', theme);
  }

  $themeBtn.addEventListener('click', function () {
    const currentTheme = document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
    setTheme(currentTheme === 'light' ? 'dark' : 'light');
  });

  /* ======== 유틸 ======== */
  function dustBadgeClass(grade) {
    const map = { '좋음': 'dust-good', '보통': 'dust-normal', '나쁨': 'dust-bad', '매우나쁨': 'dust-very-bad' };
    return map[grade] || '';
  }

  function alertTagClass(text) {
    return text.includes('경보') ? 'alert-danger' : 'alert-warn';
  }

  function escHtml(str) {
    const d = document.createElement('div');
    d.textContent = str;
    return d.innerHTML;
  }

  /* ======== 풍향 → 화살표 매핑 ======== */
  const WIND_ARROW_MAP = {
    '북풍': 180, '북북동풍': 202.5, '북동풍': 225, '동북동풍': 247.5,
    '동풍': 270, '동남동풍': 292.5, '남동풍': 315, '남남동풍': 337.5,
    '남풍': 0, '남남서풍': 22.5, '남서풍': 45, '서남서풍': 67.5,
    '서풍': 90, '서북서풍': 112.5, '북서풍': 135, '북북서풍': 157.5,
  };

  /* ======== 기상특보 발표 기준 데이터 ======== */
  const ALERT_CRITERIA = {
    '폭염': {
      '주의보': '일 최고체감온도 33℃ 이상이 2일 이상 지속될 것으로 예상될 때',
      '경보': '일 최고체감온도 35℃ 이상이 2일 이상 지속될 것으로 예상될 때'
    },
    '한파': {
      '주의보': '아침 최저기온이 전날보다 10℃ 이상 하강하여 3℃ 이하이고 평년값보다 3℃ 이상 낮을 것으로 예상될 때, 또는 아침 최저기온이 −12℃ 이하가 2일 이상 지속될 것으로 예상될 때',
      '경보': '아침 최저기온이 전날보다 15℃ 이상 하강하여 3℃ 이하이고 평년값보다 3℃ 이상 낮을 것으로 예상될 때, 또는 아침 최저기온이 −15℃ 이하가 2일 이상 지속될 것으로 예상될 때'
    },
    '호우': {
      '주의보': '3시간 강우량이 60mm 이상 또는 12시간 강우량이 110mm 이상 예상될 때',
      '경보': '3시간 강우량이 90mm 이상 또는 12시간 강우량이 180mm 이상 예상될 때'
    },
    '대설': {
      '주의보': '24시간 신적설이 5cm 이상 예상될 때',
      '경보': '24시간 신적설이 20cm 이상 예상될 때 (산지는 30cm 이상)'
    },
    '강풍': {
      '주의보': '육상에서 풍속 14m/s 이상 또는 순간풍속 20m/s 이상 예상될 때',
      '경보': '육상에서 풍속 21m/s 이상 또는 순간풍속 26m/s 이상 예상될 때'
    },
    '건조': {
      '주의보': '실효습도 35% 이하가 2일 이상 지속될 것으로 예상될 때',
      '경보': '실효습도 25% 이하가 2일 이상 지속될 것으로 예상될 때'
    },
    '태풍': {
      '주의보': '태풍으로 인하여 강풍, 풍랑, 호우, 폭풍해일 현상 등이 주의보 기준에 도달할 것으로 예상될 때',
      '경보': '태풍으로 인하여 강풍(풍속 21m/s 이상), 풍랑(파고 5m 이상), 호우(12시간 180mm 이상), 폭풍해일 현상 중 하나가 발생할 것으로 예상될 때'
    },
    '황사': {
      '주의보': '황사로 인해 1시간 평균 미세먼지(PM10) 농도가 300㎍/㎥ 이상이 2시간 이상 지속될 것으로 예상될 때',
      '경보': '황사로 인해 1시간 평균 미세먼지(PM10) 농도가 800㎍/㎥ 이상이 2시간 이상 지속될 것으로 예상될 때'
    },
    '풍랑': {
      '주의보': '해상에서 풍속 14m/s 이상이 3시간 이상 지속되거나 유의파고가 3m 이상 예상될 때',
      '경보': '해상에서 풍속 21m/s 이상이 3시간 이상 지속되거나 유의파고가 5m 이상 예상될 때'
    },
    '열대야': {
      '참고': '밤사이(18:01 ~ 다음날 09:00) 최저기온이 25℃ 이상 유지되는 현상'
    },
    '폭풍해일': {
      '주의보': '지진해일, 천조조 및 기풍 등의 영향으로 해수면이 상승하여 주의보 기준 해일고에 도달할 것으로 예상될 때',
      '경보': '지진해일, 천조조 및 기풍 등의 영향으로 해수면이 상승하여 경보 기준 해일고에 도달할 것으로 예상될 때'
    },
    '안개': {
      '주의보': '시정이 1km 미만으로 제한되어 교통 등 야외 활동에 주의가 필요할 때'
    }
  };

  /* ======== 데이터 표출 렌더러 ======== */
  function getWeatherEmoji(overview) {
    if (!overview || overview === '-') return '';
    if (overview.includes('맑')) return '☀️';
    if (overview.includes('구름')) return '⛅';
    if (overview.includes('비') || overview.includes('소나기')) return '☔';
    if (overview.includes('눈')) return '❄️';
    if (overview.includes('흐림') || overview.includes('흐리고')) return '☁️';
    return '🌤️';
  }

  function renderOverview(loc) {
    const text = loc.overview || '-';
    if (text === '-') return '-';
    const emoji = getWeatherEmoji(text);
    return `<span class="overview-cell"><span class="weather-icon-emoji">${emoji}</span> <span class="overview-text">${escHtml(text)}</span></span>`;
  }

  function renderDustPM10(loc) {
    const g = loc.dust.pm10_grade;
    const val = loc.dust.pm10;
    if (val === '-') return '-';
    const cls = dustBadgeClass(g);
    const text = (g && g !== '-') ? `${escHtml(g)} (${val})` : `${val} ㎍/㎥`;
    let html = `<span class="dust-badge ${cls}">${text}</span>`;
    if (loc.dust && loc.dust.is_fallback) {
      const info = `1차 관측소(${loc.dust.primary_station}) 점검으로 인근 관측소(${loc.dust.station_used})에서 수집`;
      html += ` <span class="fallback-badge" title="${escHtml(info)}" data-tooltip="${escHtml(info)}">!</span>`;
    }
    return html;
  }

  function renderDustPM25(loc) {
    const g = loc.dust.pm25_grade;
    const val = loc.dust.pm25;
    if (val === '-') return '-';
    const cls = dustBadgeClass(g);
    const text = (g && g !== '-') ? `${escHtml(g)} (${val})` : `${val} ㎍/㎥`;
    let html = `<span class="dust-badge ${cls}">${text}</span>`;
    if (loc.dust && loc.dust.is_fallback) {
      const info = `1차 관측소(${loc.dust.primary_station}) 점검으로 인근 관측소(${loc.dust.station_used})에서 수집`;
      html += ` <span class="fallback-badge" title="${escHtml(info)}" data-tooltip="${escHtml(info)}">!</span>`;
    }
    return html;
  }

  function renderTemp(loc) {
    const mn = loc.temperature.min;
    const mx = loc.temperature.max;
    const fl = loc.temperature.feels_like;
    if (mn === '-' && mx === '-') return '-';
    let html = `<span class="temp-value"><span class="temp-min">${escHtml(String(mn))}</span><span class="temp-sep">~</span><span class="temp-max">${escHtml(String(mx))}</span></span>`;
    if (fl && fl !== '-') {
      html += `<div class="temp-feels">현재체감 <span class="feels-num">${escHtml(String(fl))}℃</span></div>`;
    }
    return html;
  }

  function renderWind(loc) {
    if (loc.wind.direction === '-') return '-';
    const dir = loc.wind.direction;
    const deg = WIND_ARROW_MAP[dir];
    const arrowHtml = (deg !== undefined)
      ? `<span class="wind-arrow" style="display:inline-block;transform:rotate(${deg}deg)">↑</span> `
      : '';
    return `<span class="wind-dir">${arrowHtml}${escHtml(dir)}</span><br><span class="wind-speed">${escHtml(loc.wind.speed)}</span>`;
  }

  function renderRainAcc(loc) {
    const v = loc.rain_accumulated;
    if (!v || v === 0) return '-';
    return `<span class="rain-value">${v}mm</span>`;
  }

  function renderRainFcst(loc) {
    const rf = loc.rain_forecast;
    if (!rf || rf.length === 0) return '-';
    return rf.map(function (item) {
      return `<div class="rain-forecast-item">${escHtml(item.time_range)}<br><strong>${item.amount}mm</strong></div>`;
    }).join('');
  }

  function renderAlerts(loc) {
    const a = loc.alerts;
    if (!a || a.length === 0) return '-';
    return a.map(function (item) {
      if (typeof item === 'string') {
        return '<div class="alert-tag alert-tag-clickable ' + alertTagClass(item) + '" data-alert="' + escHtml(item) + '">' + escHtml(item) + '</div>';
      }
      var name = item.name || '';
      var status = item.status || '발효중';
      var effTime = item.effective_time || '';
      var isNew = item.is_new || false;
      var tagClass = alertTagClass(name);
      var statusClass = status === '예정' ? 'alert-scheduled' : '';
      var html = '<div class="alert-tag alert-tag-clickable ' + tagClass + ' ' + statusClass + '" data-alert="' + escHtml(name) + '">';
      html += escHtml(name);
      if (isNew) {
        html += '<span class="alert-badge-new">NEW</span>';
      }
      if (status === '예정' && effTime) {
        html += '<span class="alert-eff-time">' + escHtml(effTime) + ' 발효</span>';
      }
      html += '</div>';
      return html;
    }).join('');
  }

  /* ======== 행 구조 정의 ======== */
  const DUST_INFO_ICON = '<span class="dust-info-icon" data-tooltip-dust="true" title="미세먼지 등급 기준">ℹ</span>';
  const TEMP_INFO_ICON = '<span class="temp-info-icon" data-tooltip-temp="true" title="기상청 체감온도 산출 안내">ℹ</span>';
  const ROW_DEFS = [
    { cat: '개황', colspan: 2, sub: null, render: renderOverview, alertRow: false },
    { cat: '미세먼지', catHtml: '미세먼지 ' + DUST_INFO_ICON, rowspan: 2, sub: '미세', render: renderDustPM10, alertRow: false },
    { cat: null, sub: '초미세', render: renderDustPM25, alertRow: false },
    { cat: '기온(℃)', catHtml: '기온(℃) ' + TEMP_INFO_ICON, colspan: 2, sub: null, render: renderTemp, alertRow: false },
    { cat: '풍향/풍속', colspan: 2, sub: null, render: renderWind, alertRow: false },
    { cat: '일일 누적 강수량', colspan: 2, sub: null, render: renderRainAcc, alertRow: false },
    { cat: '일일 예상 강수량', colspan: 2, sub: null, render: renderRainFcst, alertRow: false },
    { cat: '기상특보', colspan: 2, sub: null, render: renderAlerts, alertRow: true },
  ];

  /* ======== 테이블 렌더링 ======== */
  function renderTable(data) {
    const locs = data.locations;
    const order = data.location_order || LOCATION_ORDER;
    let html = '';

    ROW_DEFS.forEach(function (def) {
      html += '<tr>';

      if (def.cat !== null) {
        let attrs = '';
        if (def.rowspan) attrs += ` rowspan="${def.rowspan}"`;
        if (def.colspan) attrs += ` colspan="${def.colspan}"`;
        const catLabel = def.catHtml || escHtml(def.cat);
        html += `<td class="cat-main"${attrs}>${catLabel}</td>`;
      }

      if (def.sub !== null) {
        html += `<td class="cat-sub">${escHtml(def.sub)}</td>`;
      }

      order.forEach(function (locName) {
        const loc = locs[locName];
        let cellClass = 'cell-data';
        if (def.alertRow && loc && loc.alerts && loc.alerts.length > 0) {
          cellClass += ' alert-cell';
        }
        html += `<td class="${cellClass}">`;
        if (loc) {
          html += def.render(loc);
        } else {
          html += '-';
        }
        html += '</td>';
      });

      html += '</tr>';
    });

    $body.innerHTML = html;
  }

  /* ======== 메타 업데이트 및 API 장애 배지 감지 ======== */
  function updateMeta(data) {
    if (data.date_display && data.day_of_week && $mainTitle) {
      $mainTitle.textContent = data.date_display + '.(' + data.day_of_week + ') 기상예보';
    }
    if (data.time_display && $time) {
      $time.textContent = data.time_display + ' 기준';
    }

    currentApiStatus = data.api_status || null;
    if ($apiBadge) {
      if (currentApiStatus && currentApiStatus.code && currentApiStatus.code !== 'OK') {
        $apiBadge.style.display = 'inline-flex';
        if (currentApiStatus.code === 'ERROR') {
          $apiBadge.classList.add('error');
          $apiBadge.textContent = '🚨 기상청 API 장애 발생';
        } else {
          $apiBadge.classList.remove('error');
          $apiBadge.textContent = '⚠️ 기상청 API 점검 중';
        }
      } else {
        $apiBadge.style.display = 'none';
      }
    }
  }

  /* ======== 데스크톱 알림 ======== */
  function requestNotificationPermission() {
    if ('Notification' in window && Notification.permission === 'default') {
      Notification.requestPermission();
    }
  }

  function sendDesktopNotification(title, body) {
    if ('Notification' in window && Notification.permission === 'granted') {
      try {
        new Notification(title, { body: body, icon: '/favicon.ico' });
      } catch (e) {}
    }
  }

  /* ======== 데이터 로드 (화면 렌더링 전용) ======== */
  function loadData(onSuccess) {
    const targetUrl = DATA_URL + '?t=' + Date.now();

    return fetch(targetUrl, { cache: 'no-store' })
      .then(function (res) {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
      })
      .then(function (data) {
        if (!data || !data.locations || Object.keys(data.locations).length === 0) {
          if (!currentUpdatedAt) {
            $body.innerHTML = '<tr><td colspan="10" class="loading-cell">아직 수집된 데이터가 없습니다. 잠시 후 다시 시도해 주세요.</td></tr>';
          }
          return null;
        }

        currentUpdatedAt = data.updated_at || '';
        if (currentUpdatedAt) {
          const parsed = new Date(currentUpdatedAt).getTime();
          if (!isNaN(parsed)) lastUpdatedAtTimestamp = parsed;
        }

        updateMeta(data);
        renderTable(data);
        updateHeaderTooltips(data);
        updateButtonState();

        if (onSuccess) onSuccess(data);
        return data;
      })
      .catch(function (err) {
        console.error('데이터 로드 실패:', err);
        if (!currentUpdatedAt) {
          $body.innerHTML = '<tr><td colspan="10" class="error-cell">⚠️ 데이터를 불러올 수 없습니다.</td></tr>';
        }
        return null;
      });
  }

  /* ======== 헤더 툴팁 렌더링 (방안 C 한줄 기상예보) ======== */
  function updateHeaderTooltips(data) {
    const locs = data.locations;
    const order = data.location_order || LOCATION_ORDER;
    const $thList = document.querySelectorAll('#weather-table thead .th-loc');

    $thList.forEach(function ($th, index) {
      const locName = order[index];
      if (!locName || !locs[locName]) return;

      const loc = locs[locName];
      const summary = loc.forecast_summary || `${locName} 기상 정보입니다.`;

      $th.innerHTML = `
        <div class="th-loc-container">
          <span>${escHtml(locName)}</span>
          <button class="summary-tooltip-btn" aria-label="${escHtml(locName)} 한줄 예보 보기" title="한줄 기상예보 보기">💬</button>
          <div class="summary-tooltip-card">
            ${escHtml(summary)}
          </div>
        </div>
      `;
    });

    // 터치 / 클릭 이벤트 등록 (모바일 및 클릭 대응)
    document.querySelectorAll('.th-loc-container').forEach(function (container) {
      const btn = container.querySelector('.summary-tooltip-btn');
      if (!btn) return;

      btn.addEventListener('click', function (e) {
        e.stopPropagation();
        const isActive = container.classList.contains('active');

        // 다른 툴팁 닫기
        document.querySelectorAll('.th-loc-container').forEach(function (c) {
          c.classList.remove('active');
        });

        if (!isActive) {
          container.classList.add('active');
        }
      });
    });
  }

  // 바깥 영역 클릭 시 열려있는 툴팁 닫기
  document.addEventListener('click', function () {
    document.querySelectorAll('.th-loc-container').forEach(function (c) {
      c.classList.remove('active');
    });
  });


  /* ======== 토스트 알림 ======== */
  let toastTimer = null;
  function showToast(msg, type, duration) {
    if (!$toast) return;
    $toast.textContent = msg;
    $toast.className = 'toast show ' + (type || '');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      if ($toast) $toast.className = 'toast';
    }, duration || 4000);
  }

  /* ======== 스마트 폴링 (갱신 완료 감지 — 동적 CHECK_URL 적용) ======== */
  function startPolling() {
    isPolling = true;
    pollingStartUpdatedAt = currentUpdatedAt;
    updateButtonState();

    clearInterval(pollIntervalId);
    clearTimeout(pollTimeoutId);

    // 환경에 맞게 자동 지정된 CHECK_URL 사용
    pollIntervalId = setInterval(function () {
      fetch(CHECK_URL + '?t=' + Date.now(), { cache: 'no-store' })
        .then(function (res) { return res.json(); })
        .then(function (result) {
          if (result.updated_at && pollingStartUpdatedAt && result.updated_at !== pollingStartUpdatedAt) {
            loadData(function () {
              stopPolling(true);
            });
          }
        })
        .catch(function (err) {
          console.warn('폴링 체크 실패:', err);
        });
    }, POLL_INTERVAL_MS);

    // 5분 타임아웃
    pollTimeoutId = setTimeout(function () {
      if (isPolling) {
        stopPolling(false);
        showToast('ℹ️ 갱신 처리 시간이 길어지고 있습니다. 잠시 후 새로고침해 보세요.', 'info');
      }
    }, POLL_TIMEOUT_MS);
  }

  /* ======== 알림 소리 ======== */
  function initAudioContext() {
    try {
      const AudioCtxClass = window.AudioContext || window.webkitAudioContext;
      if (!AudioCtxClass) return;
      if (!audioCtx) {
        audioCtx = new AudioCtxClass();
      }
      if (audioCtx.state === 'suspended') {
        audioCtx.resume();
      }
    } catch (e) {}
  }

  function playNotificationSound() {
    try {
      initAudioContext();
      if (!audioCtx) return;

      const playNote = function (freq, startTime, duration) {
        const osc = audioCtx.createOscillator();
        const gain = audioCtx.createGain();
        osc.type = 'sine';
        osc.frequency.setValueAtTime(freq, startTime);
        gain.gain.setValueAtTime(0.15, startTime);
        gain.gain.exponentialRampToValueAtTime(0.0001, startTime + duration);
        osc.connect(gain);
        gain.connect(audioCtx.destination);
        osc.start(startTime);
        osc.stop(startTime + duration);
      };

      const now = audioCtx.currentTime;
      playNote(523.25, now, 0.2);        // C5
      playNote(659.25, now + 0.12, 0.2);  // E5
      playNote(783.99, now + 0.24, 0.45); // G5
    } catch (e) {}
  }

  function stopPolling(isSuccess) {
    isPolling = false;
    clearInterval(pollIntervalId);
    clearTimeout(pollTimeoutId);
    localStorage.removeItem('weather_refresh_started_at');
    updateButtonState();

    if (isSuccess) {
      playNotificationSound();
      showToast('🎉 최신 날씨 데이터로 갱신이 완료되었습니다!', 'success', 5000);
      sendDesktopNotification('기상예보 대시보드', '🎉 8개 지역 날씨 데이터 갱신이 완료되었습니다!');
    }
  }

  /* ======== 갱신 버튼 이벤트 (응답 검증 로직 호환성 개선) ======== */
  let refreshAutoReloadId = null;

  function handleRefresh() {
    if (isPolling || ($btn && $btn.disabled)) return;

    initAudioContext();
    requestNotificationPermission();

    isPolling = true;
    updateButtonState();

    clearTimeout(refreshAutoReloadId);
    refreshAutoReloadId = setTimeout(function () {
      loadData();
      showToast('🔄 3분 경과 — 데이터를 자동 새로고침했습니다.', 'info', 4000);
    }, 3 * 60 * 1000);

    fetch(TRIGGER_URL, { method: 'POST' })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        if (data.message || data.success === true || data.status === 'ok') {
          localStorage.setItem('weather_refresh_started_at', String(Date.now()));
          showToast('⌛ 갱신 요청 완료! 기상청 데이터 수집 중... (약 2~3분 소요)', 'info', 6000);
          startPolling();
        } else {
          showToast('⚠️ ' + (data.error || '알 수 없는 오류'), 'error');
          isPolling = false;
          localStorage.removeItem('weather_refresh_started_at');
          updateButtonState();
        }
      })
      .catch(function () {
        showToast('⚠️ 갱신 요청 실패', 'error');
        isPolling = false;
        localStorage.removeItem('weather_refresh_started_at');
        updateButtonState();
      });
  }

  /* ======== README 모달 관리 및 이하 기존 코드 생략 없이 동일 유지 ======== */
  const $readmeBtn = document.getElementById('readme-btn');
  const $readmeModal = document.getElementById('readme-modal');
  const $modalCloseBtn = document.getElementById('modal-close-btn');
  const $modalBody = document.getElementById('modal-body');
  let readmeLoaded = false;

  function simpleMarkdownToHtml(md) {
    let html = md
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/^### (.*$)/gim, '<h3>$1</h3>')
      .replace(/^## (.*$)/gim, '<h2>$1</h2>')
      .replace(/^# (.*$)/gim, '<h1>$1</h1>')
      .replace(/^&gt; (.*$)/gim, '<blockquote>$1</blockquote>')
      .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/^---$/gim, '<hr>');

    const lines = html.split('\n');
    let inTable = false;
    let tableHtml = '';
    let resultLines = [];

    lines.forEach(function (line) {
      if (line.trim().startsWith('|') && line.trim().endsWith('|')) {
        if (!inTable) {
          inTable = true;
          tableHtml = '<table>';
        }
        const cells = line.split('|').slice(1, -1).map(function (c) { return c.trim(); });
        if (line.includes('---')) return;
        if (tableHtml === '<table>') {
          tableHtml += '<thead><tr>' + cells.map(function (c) { return '<th>' + c + '</th>'; }).join('') + '</tr></thead><tbody>';
        } else {
          tableHtml += '<tr>' + cells.map(function (c) { return '<td>' + c + '</td>'; }).join('') + '</tr>';
        }
      } else {
        if (inTable) {
          inTable = false;
          tableHtml += '</tbody></table>';
          resultLines.push(tableHtml);
          tableHtml = '';
        }
        if (line.trim().startsWith('- ')) {
          resultLines.push('<ul><li>' + line.trim().substring(2) + '</li></ul>');
        } else if (line.trim().length > 0 && !line.trim().startsWith('<h') && !line.trim().startsWith('<hr')) {
          resultLines.push('<p>' + line + '</p>');
        } else {
          resultLines.push(line);
        }
      }
    });
    if (inTable) {
      tableHtml += '</tbody></table>';
      resultLines.push(tableHtml);
    }

    return '<div class="markdown-body">' + resultLines.join('\n') + '</div>';
  }

  function loadReadme() {
    if (readmeLoaded) return;
    fetch('data/readme.txt?t=' + Date.now())
      .then(function (res) {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.text();
      })
      .then(function (text) {
        if (text.includes('NOT_FOUND') || text.includes('could not be found')) {
          throw new Error('Not found');
        }
        $modalBody.innerHTML = simpleMarkdownToHtml(text);
        readmeLoaded = true;
      })
      .catch(function () {
        fetch('README.md?t=' + Date.now())
          .then(function (res) { return res.text(); })
          .then(function (text) {
            $modalBody.innerHTML = simpleMarkdownToHtml(text);
            readmeLoaded = true;
          })
          .catch(function () {
            $modalBody.innerHTML = '<p class="error-cell">⚠️ README 안내 파일을 불러올 수 없습니다.</p>';
          });
      });
  }

  function openModal(skipReadme) {
    $readmeModal.classList.add('show');
    $readmeModal.removeAttribute('aria-hidden');
    document.body.style.overflow = 'hidden';
    if (!skipReadme) loadReadme();
    if ($modalCloseBtn) {
      setTimeout(function () { $modalCloseBtn.focus(); }, 50);
    }
  }

  function closeModal() {
    if (document.activeElement && $readmeModal.contains(document.activeElement)) {
      document.activeElement.blur();
    }
    $readmeModal.classList.remove('show');
    $readmeModal.setAttribute('aria-hidden', 'true');
    document.body.style.overflow = '';
  }

  if ($readmeBtn && $readmeModal) {
    $readmeBtn.addEventListener('click', function () {
      readmeLoaded = false;
      document.getElementById('modal-title-text').textContent = '📖 프로젝트 안내 (README)';
      openModal();
    });
    if ($modalCloseBtn) $modalCloseBtn.addEventListener('click', closeModal);
    $readmeModal.addEventListener('click', function (e) {
      if (e.target === $readmeModal) closeModal();
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && $readmeModal.classList.contains('show')) closeModal();
    });
  }

  /* ======== 데이터 진단 모달 & 로직 ======== */
  const $diagBtn = document.getElementById('diag-btn');
  const $diagModal = document.getElementById('diag-modal');
  const $diagModalCloseBtn = document.getElementById('diag-modal-close-btn');
  const $diagModalBody = document.getElementById('diag-modal-body');

  function openDiagModal() {
    if (!$diagModal) return;
    $diagModal.classList.add('show');
    $diagModal.removeAttribute('aria-hidden');
    document.body.style.overflow = 'hidden';
    runDiagnosis();
  }

  function closeDiagModal() {
    if (!$diagModal) return;
    $diagModal.classList.remove('show');
    $diagModal.setAttribute('aria-hidden', 'true');
    document.body.style.overflow = '';
  }

  function runDiagnosis() {
    if (!$diagModalBody) return;
    if ($diagBtn) $diagBtn.classList.add('loading');

    $diagModalBody.innerHTML = `
      <div class="loading-cell">
        <div class="loading-spinner"></div>
        <span>공공데이터 5개 API 실시간 상태를 측정하는 중입니다...</span>
      </div>
    `;

    fetch('/api/diag?t=' + Date.now())
      .then(function (res) { return res.json(); })
      .then(function (data) {
        if ($diagBtn) $diagBtn.classList.remove('loading');
        if (!data || !data.results) {
          $diagModalBody.innerHTML = '<p style="text-align:center; padding:1rem; color:var(--text-muted);">진단 데이터 응답 형식이 올바르지 않습니다.</p>';
          return;
        }

        let html = '<div class="diag-list">';
        data.results.forEach(function (item) {
          const badgeClass = item.ok ? 'ok' : 'fail';
          const badgeText = item.ok ? '✓ 정상' : '✗ 지연/오류';
          html += `
            <div class="diag-item">
              <span class="diag-item-name">${item.name}</span>
              <div class="diag-item-meta">
                <span class="diag-elapsed">${Number(item.elapsed).toFixed(2)}s</span>
                <span class="diag-badge ${badgeClass}">${badgeText}</span>
              </div>
            </div>
          `;
        });
        html += '</div>';

        const summaryClass = data.all_ok ? 'ok' : 'warn';
        const summaryText = data.all_ok
          ? '✅ [ALL OK] 모든 공공데이터 API 서비스가 정상 응답을 반환했습니다.'
          : '⚠️ 일부 API 서비스 응답 지연 또는 장애가 발생했습니다.';

        html += `<div class="diag-summary-box ${summaryClass}">${summaryText}</div>`;
        $diagModalBody.innerHTML = html;
      })
      .catch(function (err) {
        if ($diagBtn) $diagBtn.classList.remove('loading');
        $diagModalBody.innerHTML = `
          <div style="text-align:center; padding:1.5rem 1rem;">
            <p style="color:#ef4444; font-weight:600; margin-bottom:0.5rem;">⚠️ API 진단 호출에 실패했습니다.</p>
            <p style="font-size:0.85rem; color:var(--text-muted);">${err.message || err}</p>
          </div>
        `;
      });
  }

  if ($diagBtn && $diagModal) {
    $diagBtn.addEventListener('click', openDiagModal);
    if ($diagModalCloseBtn) $diagModalCloseBtn.addEventListener('click', closeDiagModal);
    $diagModal.addEventListener('click', function (e) {
      if (e.target === $diagModal) closeDiagModal();
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && $diagModal.classList.contains('show')) closeDiagModal();
    });
  }

  /* ======== 초기화 ======== */
  initTheme();
  $btn.addEventListener('click', handleRefresh);
  loadData(function () {
    // 새로고침(F5) 시 3분 이내 요청 이력이 있다면 갱신 중 상태 및 폴링 감지 자동 복원
    const savedRefreshTime = localStorage.getItem('weather_refresh_started_at');
    if (savedRefreshTime) {
      const elapsed = Date.now() - Number(savedRefreshTime);
      if (elapsed < 3 * 60 * 1000) {
        startPolling();
      } else {
        localStorage.removeItem('weather_refresh_started_at');
      }
    }
  });

  setInterval(function () {
    if (!isPolling) loadData();
  }, AUTO_RELOAD_MS);

  // 1초마다 버튼 쿨타임 및 진행 상태 갱신
  setInterval(updateButtonState, 1000);

  /* ======== API 상태 경고 배지 클릭 → 상세 안내 팝업 ======== */
  if ($apiBadge) {
    $apiBadge.addEventListener('click', function () {
      if (!currentApiStatus) return;
      const msg = currentApiStatus.message || '공공데이터포털 API 수집 중 일부 오류가 발생했습니다.';
      const failed = currentApiStatus.failed_services || [];
      const failedListHtml = failed.length > 0
        ? '<ul style="margin-top:0.4rem;padding-left:1.2rem;color:var(--text-primary);font-size:0.88rem;">' + failed.map(function(s) { return '<li style="margin-bottom:0.2rem;">' + escHtml(s) + '</li>'; }).join('') + '</ul>'
        : '<p style="font-size:0.88rem;color:var(--text-secondary);">일부 외부 기상 API 수집 응답 지연</p>';

      const popupHtml = '<div class="alert-popup-content">'
        + '<h3>⚠️ 공공데이터포털 API 상태 안내</h3>'
        + '<div class="alert-criteria-item active">'
        + '<strong class="alert-warn-label">' + escHtml(currentApiStatus.code || 'WARNING') + '</strong>'
        + '<p style="margin-top:0.5rem;font-size:0.92rem;line-height:1.5;">' + escHtml(msg) + '</p>'
        + '</div>'
        + '<div style="margin-top:1rem;background:rgba(0,0,0,0.03);padding:0.8rem;border-radius:8px;">'
        + '<h4 style="font-size:0.86rem;color:var(--text-secondary);margin-bottom:0.3rem;">🚨 응답 지연/오류 서비스:</h4>'
        + failedListHtml
        + '</div>'
        + '<p style="margin-top:1rem;font-size:0.8rem;color:var(--text-dim);line-height:1.4;">'
        + '💡 대시보드는 이전에 성공적으로 수집된 최신 관측 데이터를 안전하게 지속 표출하고 있습니다. 공공데이터포털 서버가 정상화되면 다음 주기에 자동으로 최신화됩니다.'
        + '</p>'
        + '</div>';

      $modalBody.innerHTML = popupHtml;
      const titleEl = document.getElementById('modal-title-text');
      if (titleEl) titleEl.textContent = '⚠️ 공공데이터포털 API 상태 안내';
      readmeLoaded = false;
      openModal(true);
    });
  }

  /* ======== 기상특보 태그 클릭 → 기준 팝업 ======== */
  document.addEventListener('click', function (e) {
    const tag = e.target.closest('.alert-tag-clickable');
    if (!tag) return;

    const alertText = tag.getAttribute('data-alert') || '';
    let matchedType = '';
    let matchedLevel = '';

    Object.keys(ALERT_CRITERIA).forEach(function (type) {
      if (alertText.includes(type)) {
        matchedType = type;
        if (alertText.includes('경보')) matchedLevel = '경보';
        else if (alertText.includes('주의보')) matchedLevel = '주의보';
      }
    });

    let popupHtml = '';
    if (matchedType && ALERT_CRITERIA[matchedType]) {
      const criteria = ALERT_CRITERIA[matchedType];
      popupHtml = '<div class="alert-popup-content">';
      Object.keys(criteria).forEach(function (levelKey) {
        const text = criteria[levelKey];
        let labelClass = 'alert-info-label';
        if (levelKey === '주의보') labelClass = 'alert-warn-label';
        else if (levelKey === '경보') labelClass = 'alert-danger-label';

        const isActive = matchedLevel ? (matchedLevel === levelKey) : true;
        popupHtml += '<div class="alert-criteria-item' + (isActive ? ' active' : '') + '">';
        popupHtml += '<strong class="' + labelClass + '">' + escHtml(levelKey) + '</strong>';
        popupHtml += '<p>' + escHtml(text) + '</p>';
        popupHtml += '</div>';
      });
      popupHtml += '</div>';
    } else {
      popupHtml = '<div class="alert-popup-content"><div class="alert-criteria-item active"><strong class="alert-warn-label">발표 현황</strong><p>' + escHtml(alertText) + ' 발효 중입니다.</p></div></div>';
    }

    $modalBody.innerHTML = popupHtml;
    const titleEl = document.getElementById('modal-title-text');
    if (titleEl) titleEl.textContent = '⚠️ ' + alertText + ' 발표 기준';
    readmeLoaded = false;
    openModal(true);
  });

  /* ======== 미세먼지 인포 아이콘 클릭 → 기준 팝업 ======== */
  document.addEventListener('click', function (e) {
    if (!e.target.closest('.dust-info-icon')) return;

    const dustHtml = '<div class="alert-popup-content">'
      + '<h3>ℹ️ 미세먼지 등급 기준</h3>'
      + '<table class="dust-criteria-table">'
      + '<thead><tr><th>등급</th><th>미세먼지 (PM10)</th><th>초미세먼지 (PM2.5)</th></tr></thead>'
      + '<tbody>'
      + '<tr class="dust-good"><td>좋음</td><td>0 ~ 30 ㎍/㎥</td><td>0 ~ 15 ㎍/㎥</td></tr>'
      + '<tr class="dust-normal"><td>보통</td><td>31 ~ 80 ㎍/㎥</td><td>16 ~ 35 ㎍/㎥</td></tr>'
      + '<tr class="dust-bad"><td>나쁨</td><td>81 ~ 150 ㎍/㎥</td><td>36 ~ 75 ㎍/㎥</td></tr>'
      + '<tr class="dust-very-bad"><td>매우나쁨</td><td>151 ㎍/㎥ 이상</td><td>76 ㎍/㎥ 이상</td></tr>'
      + '</tbody></table>'
      + '<p style="margin-top:0.8rem;font-size:0.78rem;color:var(--text-dim)">출처: 환경부 에어코리아</p>'
      + '</div>';

    $modalBody.innerHTML = dustHtml;
    document.getElementById('modal-title-text').textContent = 'ℹ️ 미세먼지 등급 기준';
    readmeLoaded = false;
    openModal(true);
  });

  /* ======== 체감온도 인포 아이콘 클릭 → 산출 방식 팝업 ======== */
  document.addEventListener('click', function (e) {
    if (!e.target.closest('.temp-info-icon')) return;

    const tempHtml = '<div class="alert-popup-content">'
      + '<h3>🌡️ 기상청 체감온도 산출 및 안내</h3>'
      + '<div class="temp-info-box">'
      + '<p>본 대시보드는 <strong>대한민국 기상청(KMA) 공식 체감온도 산출식</strong>을 적용하여 실시간 기온, 습도, 풍속 데이터를 바탕으로 체감온도를 자동 계산합니다.</p>'
      + '<hr style="border:none;border-top:1px solid var(--border);margin:0.8rem 0;">'
      + '<h4>☀️ 여름철 (5월~9월 또는 기온 ≥ 20℃)</h4>'
      + '<p style="margin-bottom:0.6rem;">기온과 <strong>상대습도</strong>(Stull 습구온도 T<sub>w</sub> 추정식)를 결합한 기상청 습도 체감온도 공식을 적용합니다.<br>'
      + '<span style="font-size:0.8rem;color:var(--text-secondary);display:inline-block;margin-top:0.2rem;">※ 습도가 높으면 실제 기온(28~29℃)이 낮더라도 체감온도는 33℃/35℃ 이상으로 급상승하여 폭염특보 발효 기준이 됩니다.</span></p>'
      + '<h4>❄️ 겨울철 (10월~4월 또는 기온 ≤ 10℃ & 풍속 ≥ 1.3m/s)</h4>'
      + '<p style="margin-bottom:0.6rem;">기온과 <strong>풍속</strong>(바람)을 반영한 WMO/JAG/TI 바람 체감온도 공식을 적용합니다.</p>'
      + '<h4>🍃 온화한 기온대 (10℃ ~ 20℃)</h4>'
      + '<p>체감온도가 기온과 거의 유사하여 기온 수치를 그대로 표출합니다.</p>'
      + '<p style="margin-top:0.8rem;font-size:0.82rem;color:var(--accent);background:rgba(37,99,235,0.08);padding:0.6rem 0.8rem;border-radius:8px;border:1px solid rgba(37,99,235,0.2);">'
      + '💡 <strong>[현재체감 vs 폭염특보 참고]</strong><br>'
      + '표의 <strong>"현재체감"</strong>은 지금 이 순간의 실시간 관측 체감온도이며, <strong>폭염특보</strong>는 오늘 낮 최고 예상 <strong>"일 최고 체감온도(33℃/35℃ 이상)"</strong>를 기준으로 하루 동안 발효·유지됩니다.'
      + '</p>'
      + '</div>'
      + '</div>';

    $modalBody.innerHTML = tempHtml;
    document.getElementById('modal-title-text').textContent = '🌡️ 체감온도 산출 및 안내';
    readmeLoaded = false;
    openModal(true);
  });
})();
