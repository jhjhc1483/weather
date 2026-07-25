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

  /* ======== 풍향 → 화살표 매핑 (바람이 불어오는 방향 → 이동 방향 화살표) ======== */
  const WIND_ARROW_MAP = {
    '북풍': 180, '북북동풍': 202.5, '북동풍': 225, '동북동풍': 247.5,
    '동풍': 270, '동남동풍': 292.5, '남동풍': 315, '남남동풍': 337.5,
    '남풍': 0, '남남서풍': 22.5, '남서풍': 45, '서남서풍': 67.5,
    '서풍': 90, '서북서풍': 112.5, '북서풍': 135, '북북서풍': 157.5,
  };

  /* ======== 기상특보 발표 기준 데이터 ======== */
  const ALERT_CRITERIA = {
    '폭염': { '주의보': '일 최고체감온도 33℃ 이상이 2일 이상 지속될 것으로 예상될 때', '경보': '일 최고체감온도 35℃ 이상이 2일 이상 지속될 것으로 예상될 때' },
    '한파': { '주의보': '아침 최저기온이 전날보다 10℃ 이상 하강하여 3℃ 이하이고 평년값보다 3℃ 이상 낮을 것으로 예상될 때, 또는 아침 최저기온이 −12℃ 이하가 2일 이상 지속될 것으로 예상될 때', '경보': '아침 최저기온이 전날보다 15℃ 이상 하강하여 3℃ 이하이고 평년값보다 3℃ 이상 낮을 것으로 예상될 때, 또는 아침 최저기온이 −15℃ 이하가 2일 이상 지속될 것으로 예상될 때' },
    '호우': { '주의보': '6시간 강우량 70mm 이상 또는 12시간 강우량 110mm 이상 예상될 때', '경보': '6시간 강우량 110mm 이상 또는 12시간 강우량 180mm 이상 예상될 때' },
    '대설': { '주의보': '24시간 신적설이 5cm 이상 예상될 때', '경보': '24시간 신적설이 20cm 이상 예상될 때 (산지는 30cm 이상)' },
    '강풍': { '주의보': '육상에서 풍속 14m/s 이상 또는 순간풍속 20m/s 이상 예상될 때', '경보': '육상에서 풍속 21m/s 이상 또는 순간풍속 26m/s 이상 예상될 때' },
    '건조': { '주의보': '실효습도 35% 이하가 2일 이상 지속될 것으로 예상될 때', '경보': '실효습도 25% 이하가 2일 이상 지속될 것으로 예상될 때' },
    '태풍': { '주의보': '태풍으로 인하여 풍속 17m/s 이상 또는 강우량 100mm 이상 예상될 때', '경보': '태풍으로 인하여 풍속 25m/s 이상 또는 강우량 200mm 이상 예상될 때' },
    '황사': { '주의보': 'PM10 농도 400㎍/㎥ 이상이 2시간 이상 지속될 것으로 예상될 때', '경보': 'PM10 농도 800㎍/㎥ 이상이 2시간 이상 지속될 것으로 예상될 때' },
    '풍랑': { '주의보': '해상에서 풍속 14m/s 이상이 3시간 이상 또는 유의파고 3m 이상 예상될 때', '경보': '해상에서 풍속 21m/s 이상이 3시간 이상 또는 유의파고 5m 이상 예상될 때' },
    '호우': { '주의보': '6시간 강우량 70mm 이상 또는 12시간 강우량 110mm 이상 예상될 때', '경보': '6시간 강우량 110mm 이상 또는 12시간 강우량 180mm 이상 예상될 때' },
  };

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
    return a.map(function (t) {
      return `<div class="alert-tag alert-tag-clickable ${alertTagClass(t)}" data-alert="${escHtml(t)}">${escHtml(t)}</div>`;
    }).join('');
  }

  /* ======== 행 구조 정의 (미세먼지 외 항목 카테고리 셀 병합) ======== */
  const DUST_INFO_ICON = '<span class="dust-info-icon" data-tooltip-dust="true" title="미세먼지 등급 기준">ℹ</span>';
  const ROW_DEFS = [
    { cat: '개황', colspan: 2, sub: null, render: renderOverview, alertRow: false },
    { cat: '미세먼지', catHtml: '미세먼지 ' + DUST_INFO_ICON, rowspan: 2, sub: '미세', render: renderDustPM10, alertRow: false },
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

        updateMeta(data);
        renderTable(data);

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

  /* ======== 스마트 폴링 (갱신 완료 감지 — /api/check 서버리스 함수로 CDN 캐시 우회) ======== */
  function startPolling() {
    if (isPolling) return;
    isPolling = true;
    pollingStartUpdatedAt = currentUpdatedAt; // 갱신 요청 시점 스냅샷
    $btn.classList.add('loading');

    clearInterval(pollIntervalId);
    clearTimeout(pollTimeoutId);

    // 20초마다 /api/check로 타임스탬프 확인 (CDN 캐시 100% 우회)
    pollIntervalId = setInterval(function () {
      fetch('/api/check?t=' + Date.now(), { cache: 'no-store' })
        .then(function (res) { return res.json(); })
        .then(function (result) {
          if (result.updated_at && pollingStartUpdatedAt && result.updated_at !== pollingStartUpdatedAt) {
            // 새 데이터 감지 → 화면에 반영하고 폴링 종료
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
  let refreshAutoReloadId = null;

  function handleRefresh() {
    initAudioContext(); // 클릭 시점에 오디오 컨텍스트 사전 활성화 (브라우저 Autoplay 락 해제!)
    requestNotificationPermission();
    $btn.classList.add('loading');

    // 3분 후 자동 새로고침 (Actions 완료 후 최신 데이터 보장)
    clearTimeout(refreshAutoReloadId);
    refreshAutoReloadId = setTimeout(function () {
      loadData();
      showToast('🔄 3분 경과 — 데이터를 자동 새로고침했습니다.', 'info', 4000);
    }, 3 * 60 * 1000);

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

  /* ======== 기상특보 태그 클릭 → 기준 팝업 (이벤트 위임) ======== */
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
      popupHtml += '<h3>⚠️ ' + escHtml(matchedType) + ' 발표 기준</h3>';
      if (criteria['주의보']) {
        popupHtml += '<div class="alert-criteria-item' + (matchedLevel === '주의보' ? ' active' : '') + '"><strong class="alert-warn-label">주의보</strong><p>' + escHtml(criteria['주의보']) + '</p></div>';
      }
      if (criteria['경보']) {
        popupHtml += '<div class="alert-criteria-item' + (matchedLevel === '경보' ? ' active' : '') + '"><strong class="alert-danger-label">경보</strong><p>' + escHtml(criteria['경보']) + '</p></div>';
      }
      popupHtml += '</div>';
    } else {
      popupHtml = '<div class="alert-popup-content"><p>' + escHtml(alertText) + '</p></div>';
    }

    // 모달 재활용
    $modalBody.innerHTML = popupHtml;
    document.getElementById('modal-title-text').textContent = '⚠️ 기상특보 발표 기준';
    readmeLoaded = false;
    openModal();
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
    openModal();
  });
})();
