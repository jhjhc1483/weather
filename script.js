/**
 * 기상예보 대시보드 — 프론트엔드 스크립트 (완료 알림 및 스마트 폴링 지원)
 *
 * - data/weather.json fetch 및 동적 표출
 * - 다크 / 라이트 모드 전환 (localStorage)
 * - 갱신 버튼 클릭 시 백그라운드 트리거 및 갱신 완료 자동 감지 & 토스트 알림
 */

(function () {
  'use strict';

  /* ======== 상수 ======== */
  const DATA_URL = 'data/weather.json';
  const TRIGGER_URL = '/api/trigger';
  const AUTO_RELOAD_MS = 5 * 60 * 1000;  // 5분 자동 백그라운드 재로드
  const POLL_INTERVAL_MS = 20000;        // 갱신 요청 후 20초 간격 감지
  const POLL_TIMEOUT_MS = 5 * 60 * 1000; // 5분 후 감지 중단

  const LOCATION_ORDER = ['양평', '경산', '사천', '함안', '성주', '세종', '계룡', '임실'];

  /* ======== DOM ======== */
  const $body = document.getElementById('weather-body');
  const $mainTitle = document.getElementById('main-title');
  const $time = document.getElementById('time-display');
  const $btn = document.getElementById('refresh-btn');
  const $toast = document.getElementById('toast');
  const $themeBtn = document.getElementById('theme-toggle-btn');

  /* ======== 상태 변수 ======== */
  let currentUpdatedAt = '';
  let pollingStartUpdatedAt = '';
  let isPolling = false;
  let pollIntervalId = null;
  let pollTimeoutId = null;
  let audioCtx = null;

  /* ======== 테마 관리 ======== */
  function initTheme() {
    const savedTheme = localStorage.getItem('theme') || 'dark';
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

  /* ======== 데이터 표출 렌더러 ======== */
  function renderOverview(loc) {
    return `<span class="overview-text">${escHtml(loc.overview)}</span>`;
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

  /* ======== 행 구조 정의 (미세먼지 외 항목 카테고리 셀 병합) ======== */
  const ROW_DEFS = [
    { cat: '개황', colspan: 2, sub: null, render: renderOverview, alertRow: false },
    { cat: '미세먼지', rowspan: 2, sub: '미세', render: renderDustPM10, alertRow: false },
    { cat: null, sub: '초미세', render: renderDustPM25, alertRow: false },
    { cat: '기온(℃)', colspan: 2, sub: null, render: renderTemp, alertRow: false },
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
        html += `<td class="cat-main"${attrs}>${escHtml(def.cat)}</td>`;
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

  /* ======== 메타 업데이트 ======== */
  function updateMeta(data) {
    if (data.date_display && data.day_of_week && $mainTitle) {
      $mainTitle.textContent = data.date_display + '.(' + data.day_of_week + ') 기상예보';
    }
    if (data.time_display && $time) {
      $time.textContent = data.time_display + ' 기준';
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

  /* ======== 데이터 로드 및 갱신 감지 ======== */
  function loadData(onSuccess) {
    // Vercel / 브라우저 CDN 캐시를 완전히 무력화하는 강력한 캐시 버스팅 URL
    const targetUrl = DATA_URL + '?t=' + Date.now() + '&r=' + Math.random().toString(36).substring(2, 7);

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

        const baseline = isPolling ? pollingStartUpdatedAt : currentUpdatedAt;
        const isNewData = baseline && data.updated_at && (data.updated_at !== baseline);
        currentUpdatedAt = data.updated_at || '';

        updateMeta(data);
        renderTable(data);

        if (onSuccess) onSuccess(isNewData, data);
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

  /* ======== 토스트 알림 ======== */
  let toastTimer = null;
  function showToast(msg, type, duration) {
    $toast.textContent = msg;
    $toast.className = 'toast show ' + (type || '');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      $toast.className = 'toast';
    }, duration || 4000);
  }

  /* ======== 스마트 폴링 (갱신 완료 감지) ======== */
  function startPolling() {
    if (isPolling) return;
    isPolling = true;
    pollingStartUpdatedAt = currentUpdatedAt; // 갱신 요청 시점 스냅샷
    $btn.classList.add('loading');

    clearInterval(pollIntervalId);
    clearTimeout(pollTimeoutId);

    // 20초마다 weather.json 타임스탬프 체크 (no-store 캐시 우회)
    pollIntervalId = setInterval(function () {
      loadData(function (isNewData, data) {
        if (isNewData) {
          stopPolling(true);
        }
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

  /* ======== 알림 소리 (Web Audio API 차임 벨 - Autoplay 락 해제 포함) ======== */
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
      playNote(783.99, now + 0.24, 0.45); // G5 (맑은 3음계 알림음)
    } catch (e) {}
  }

  function stopPolling(isSuccess) {
    isPolling = false;
    clearInterval(pollIntervalId);
    clearTimeout(pollTimeoutId);
    $btn.classList.remove('loading');

    if (isSuccess) {
      playNotificationSound();
      showToast('🎉 최신 날씨 데이터로 갱신이 완료되었습니다!', 'success', 5000);
      sendDesktopNotification('기상예보 대시보드', '🎉 8개 지역 날씨 데이터 갱신이 완료되었습니다!');
    }
  }

  /* ======== 갱신 버튼 이벤트 ======== */
  function handleRefresh() {
    initAudioContext(); // 클릭 시점에 오디오 컨텍스트 사전 활성화 (브라우저 Autoplay 락 해제!)
    requestNotificationPermission();
    $btn.classList.add('loading');

    fetch(TRIGGER_URL, { method: 'POST' })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        if (data.message) {
          showToast('⌛ 갱신 요청 완료! 기상청 데이터 수집 중... (약 1~2분 소요)', 'info', 6000);
          startPolling();
        } else {
          showToast('⚠️ ' + (data.error || '알 수 없는 오류'), 'error');
          $btn.classList.remove('loading');
        }
      })
      .catch(function () {
        showToast('⚠️ 갱신 요청 실패', 'error');
        $btn.classList.remove('loading');
      });
  }

  /* ======== README 모달 관리 ======== */
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

  function openModal() {
    $readmeModal.classList.add('show');
    $readmeModal.setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';
    loadReadme();
  }

  function closeModal() {
    $readmeModal.classList.remove('show');
    $readmeModal.setAttribute('aria-hidden', 'true');
    document.body.style.overflow = '';
  }

  if ($readmeBtn && $readmeModal) {
    $readmeBtn.addEventListener('click', openModal);
    if ($modalCloseBtn) $modalCloseBtn.addEventListener('click', closeModal);
    $readmeModal.addEventListener('click', function (e) {
      if (e.target === $readmeModal) closeModal();
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && $readmeModal.classList.contains('show')) closeModal();
    });
  }

  /* ======== 초기화 ======== */
  initTheme();
  $btn.addEventListener('click', handleRefresh);
  loadData();
  setInterval(function () {
    if (!isPolling) loadData();
  }, AUTO_RELOAD_MS);
})();
