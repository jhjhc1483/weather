/**
 * 기상예보 대시보드 — 프론트엔드 스크립트
 *
 * - data/weather.json fetch 및 동적 표출
 * - 다크 / 라이트 모드 전환 (localStorage 저장)
 * - 5분 자동 갱신 및 지금 갱신 트리거
 */

(function () {
  'use strict';

  /* ======== 상수 ======== */
  const DATA_URL = 'data/weather.json';
  const TRIGGER_URL = '/api/trigger';
  const AUTO_RELOAD_MS = 5 * 60 * 1000;
  const COOLDOWN_MS = 60 * 1000;

  const LOCATION_ORDER = ['양평', '경산', '사천', '함안', '성주', '세종', '계룡', '임실'];

  /* ======== DOM ======== */
  const $body = document.getElementById('weather-body');
  const $date = document.getElementById('date-display');
  const $time = document.getElementById('time-display');
  const $btn = document.getElementById('refresh-btn');
  const $toast = document.getElementById('toast');
  const $themeBtn = document.getElementById('theme-toggle-btn');
  const $themeText = document.getElementById('theme-text');

  /* ======== 테마 관리 ======== */
  function initTheme() {
    const savedTheme = localStorage.getItem('theme') || 'dark';
    setTheme(savedTheme);
  }

  function setTheme(theme) {
    if (theme === 'light') {
      document.documentElement.setAttribute('data-theme', 'light');
      $themeText.textContent = '다크 모드';
    } else {
      document.documentElement.removeAttribute('data-theme');
      $themeText.textContent = '라이트 모드';
    }
    localStorage.setItem('theme', theme);
  }

  $themeBtn.addEventListener('click', function () {
    const currentTheme = document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
    const nextTheme = currentTheme === 'light' ? 'dark' : 'light';
    setTheme(nextTheme);
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

  /* ======== 셀 렌더러 ======== */
  function renderOverview(loc) {
    return `<span class="overview-text">${escHtml(loc.overview)}</span>`;
  }

  function renderDustPM10(loc) {
    const g = loc.dust.pm10_grade;
    const cls = dustBadgeClass(g);
    const val = loc.dust.pm10;
    if (val === '-') return '-';
    return `<span class="dust-badge ${cls}">${escHtml(g)} (${val})</span>`;
  }

  function renderDustPM25(loc) {
    const g = loc.dust.pm25_grade;
    const cls = dustBadgeClass(g);
    const val = loc.dust.pm25;
    if (val === '-') return '-';
    return `<span class="dust-badge ${cls}">${escHtml(g)} (${val})</span>`;
  }

  function renderTemp(loc) {
    const mn = loc.temperature.min;
    const mx = loc.temperature.max;
    if (mn === '-' && mx === '-') return '-';
    return `<span class="temp-value"><span class="temp-min">${escHtml(String(mn))}</span><span class="temp-sep">~</span><span class="temp-max">${escHtml(String(mx))}</span></span>`;
  }

  function renderWind(loc) {
    if (loc.wind.direction === '-') return '-';
    return `<span class="wind-dir">${escHtml(loc.wind.direction)}</span><br><span class="wind-speed">${escHtml(loc.wind.speed)}</span>`;
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
    return a.map(function (t) {
      return `<div class="alert-tag ${alertTagClass(t)}">${escHtml(t)}</div>`;
    }).join('');
  }

  /* ======== 행 구조 정의 ======== */
  var ROW_DEFS = [
    { cat: '개황', sub: '', render: renderOverview, alertRow: false },
    { cat: '미세먼지', sub: '미세', render: renderDustPM10, rowspan: 2, alertRow: false },
    { cat: null, sub: '초미세', render: renderDustPM25, alertRow: false },
    { cat: '기온', sub: '', render: renderTemp, alertRow: false },
    { cat: '풍향/풍속', sub: '', render: renderWind, alertRow: false },
    { cat: '일일 누적 강수량', sub: '', render: renderRainAcc, alertRow: false },
    { cat: '일일 예상 강수량', sub: '', render: renderRainFcst, alertRow: false },
    { cat: '기상특보', sub: '', render: renderAlerts, alertRow: true },
  ];

  /* ======== 테이블 렌더링 ======== */
  function renderTable(data) {
    var locs = data.locations;
    var order = data.location_order || LOCATION_ORDER;
    var html = '';

    ROW_DEFS.forEach(function (def) {
      html += '<tr>';

      if (def.cat !== null) {
        var rs = def.rowspan ? ` rowspan="${def.rowspan}"` : '';
        html += `<td class="cat-main"${rs}>${escHtml(def.cat)}</td>`;
      }

      html += `<td class="cat-sub">${escHtml(def.sub)}</td>`;

      order.forEach(function (locName) {
        var loc = locs[locName];
        var cellClass = 'cell-data';
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

  /* ======== 메타 업데이트 ======== */
  function updateMeta(data) {
    if (data.date_display && data.day_of_week) {
      $date.textContent = data.date_display + ' (' + data.day_of_week + ') 기상예보';
    }
    if (data.time_display) {
      $time.textContent = data.time_display + ' 기준';
    }
  }

  /* ======== 데이터 로드 ======== */
  function loadData() {
    fetch(DATA_URL + '?t=' + Date.now())
      .then(function (res) {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
      })
      .then(function (data) {
        if (!data.locations || Object.keys(data.locations).length === 0) {
          $body.innerHTML = '<tr><td colspan="10" class="loading-cell">아직 수집된 데이터가 없습니다. 잠시 후 다시 시도해 주세요.</td></tr>';
          return;
        }
        updateMeta(data);
        renderTable(data);
      })
      .catch(function (err) {
        console.error('데이터 로드 실패:', err);
        $body.innerHTML = '<tr><td colspan="10" class="error-cell">⚠️ 데이터를 불러올 수 없습니다.</td></tr>';
      });
  }

  /* ======== 토스트 ======== */
  var toastTimer = null;
  function showToast(msg, type) {
    $toast.textContent = msg;
    $toast.className = 'toast show ' + (type || '');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      $toast.className = 'toast';
    }, 3500);
  }

  /* ======== 갱신 버튼 ======== */
  var cooldownTimer = null;

  function handleRefresh() {
    $btn.classList.add('loading');

    fetch(TRIGGER_URL, { method: 'POST' })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        if (data.message) {
          showToast('✅ ' + data.message, 'success');
        } else {
          showToast('⚠️ ' + (data.error || '알 수 없는 오류'), 'error');
        }
      })
      .catch(function () {
        showToast('⚠️ 갱신 요청 실패', 'error');
      })
      .finally(function () {
        $btn.classList.remove('loading');
        $btn.classList.add('cooldown');
        clearTimeout(cooldownTimer);
        cooldownTimer = setTimeout(function () {
          $btn.classList.remove('cooldown');
        }, COOLDOWN_MS);
      });
  }

  /* ======== 초기화 ======== */
  initTheme();
  $btn.addEventListener('click', handleRefresh);
  loadData();
  setInterval(loadData, AUTO_RELOAD_MS);
})();
