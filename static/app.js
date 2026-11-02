// MoodFit 화면 — 데이터는 모두 서버 API로 주고받는다. 브라우저에는 로그인 토큰만 저장한다.

const $ = sel => document.querySelector(sel);
const $$ = sel => [...document.querySelectorAll(sel)];

const OPTIONS = {
  mood: ['신남', '편안함', '진지함', '설렘', '피곤함', '자신감'],
  occasion: ['출근', '데이트', '친구 약속', '면접', '외출', '운동', '여행', '집에서'],
  style: ['캐주얼', '포멀', '스트릿', '미니멀', '빈티지', '스포티'],
  category: ['상의', '하의', '아우터', '신발', '가방', '액세서리'],
  season: ['사계절', '봄', '여름', '가을', '겨울'],
  gender: ['선택 안 함', '남성', '여성'],
  body_type: ['슬림', '보통', '근육형', '통통'],
  skin_tone: ['밝음', '중간', '어두움'],
  personal_color: ['모름', '봄 웜톤', '여름 쿨톤', '가을 웜톤', '겨울 쿨톤'],
};
const MOOD_EMOJI = { 신남: '😆', 편안함: '😌', 진지함: '🧐', 설렘: '🥰', 피곤함: '😪', 자신감: '😎' };
const CATEGORY_EMOJI = { 상의: '👕', 하의: '👖', 아우터: '🧥', 신발: '👟', 가방: '👜', 액세서리: '💍', 미분류: '🏷' };
const SLOT_LABEL = { top: '상의', bottom: '하의', outer: '아우터', shoes: '신발', accessory: '액세서리' };
const DEMO = { email: 'demo@moodfit.app', password: 'moodfit-demo' };

const state = {
  token: null, user: null, items: [], filter: '전체',
  weather: null,                       // 서버에서 받은 날씨 (없으면 직접 입력값 사용)
  mood: '편안함', occasions: new Set(['출근']), style: '캐주얼',
  lastPicks: [], editingId: null, photoBlob: null, itemFields: {},
};

// ── 공통 ───────────────────────────────────────────────
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// LLM 답변의 간단한 마크다운(제목, 굵게, 목록)만 보기 좋게 바꾼다. 먼저 escape해서 HTML 주입을 막는다.
function formatAnswer(text) {
  return esc(text)
    .replace(/^#{1,4}\s*(.+)\n*/gm, '<h4>$1</h4>')
    .replace(/^\s*(-{3,}|\*{3,})\s*$/gm, '')
    .replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
    .replace(/^\s*[-*]\s+/gm, '• ')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

function storage(action, value) {   // 사생활 보호 모드 등에서 localStorage가 막혀도 앱이 죽지 않게
  try {
    if (action === 'get') return localStorage.getItem('moodfit_token');
    if (action === 'set') localStorage.setItem('moodfit_token', value);
    if (action === 'del') localStorage.removeItem('moodfit_token');
  } catch { return null; }
}

async function api(path, { method = 'GET', body, form } = {}) {
  const headers = {};
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const res = await fetch(path, { method, headers, body: form ?? (body !== undefined ? JSON.stringify(body) : undefined) });
  if (res.status === 401 && state.token) { logout(); throw new Error('로그인이 만료됐어요. 다시 로그인해 주세요.'); }
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = Array.isArray(data.detail) ? '입력값을 확인해 주세요.' : data.detail;
    throw new Error(detail || `요청이 실패했어요 (${res.status})`);
  }
  return data;
}

// 칩(버튼 묶음): single이면 하나만, 아니면 여러 개 선택. max로 개수 제한.
function renderChips(el, values, { selected, onChange, labels = {}, max } = {}) {
  const single = el.hasAttribute('data-single');
  const isOn = v => single ? selected() === v : selected().has(v);
  el.innerHTML = values.map(v =>
    `<button type="button" class="chip${isOn(v) ? ' active' : ''}" data-value="${esc(v)}">${labels[v] ? labels[v] + ' ' : ''}${esc(v)}</button>`
  ).join('');
  el.onclick = e => {
    const btn = e.target.closest('.chip');
    if (!btn) return;
    const v = btn.dataset.value;
    if (single) onChange(v);
    else {
      const set = selected();
      if (set.has(v)) set.delete(v);
      else if (!max || set.size < max) set.add(v);
      onChange(set);
    }
    renderChips(el, values, { selected, onChange, labels, max });
  };
}

// ── 로그인 ─────────────────────────────────────────────
function showAuth() { $('#appScreen').hidden = true; $('#authScreen').hidden = false; }

async function enterApp(token, user) {
  state.token = token; state.user = user;
  storage('set', token);
  $('#authScreen').hidden = true; $('#appScreen').hidden = false;
  $('#nickname').textContent = `${user.nickname}님`;
  fillProfile();
  await loadItems();
}

function logout() {
  state.token = null; state.user = null; state.items = [];
  storage('del');
  showAuth();
}

async function submitAuth(form, path) {
  const err = form.querySelector('[data-error]');
  err.textContent = '';
  const body = Object.fromEntries(new FormData(form));
  const btn = form.querySelector('button[type=submit]');
  btn.disabled = true;
  try {
    const { token, user } = await api(path, { method: 'POST', body });
    form.reset();
    await enterApp(token, user);
  } catch (e) { err.textContent = e.message; }
  finally { btn.disabled = false; }
}

function setupAuth() {
  $$('[data-auth-tab]').forEach(b => b.onclick = () => {
    $$('[data-auth-tab]').forEach(x => x.classList.toggle('active', x === b));
    $('#loginForm').hidden = b.dataset.authTab !== 'login';
    $('#signupForm').hidden = b.dataset.authTab !== 'signup';
  });
  $('#loginForm').onsubmit = e => { e.preventDefault(); submitAuth(e.target, '/api/auth/login'); };
  $('#signupForm').onsubmit = e => { e.preventDefault(); submitAuth(e.target, '/api/auth/signup'); };
  $('#demoBtn').onclick = () => {
    const f = $('#loginForm');
    f.email.value = DEMO.email; f.password.value = DEMO.password;
    submitAuth(f, '/api/auth/login');
  };
  $('#logoutBtn').onclick = logout;
}

// ── 탭 ─────────────────────────────────────────────────
function showTab(name) {
  $$('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
  $$('.tab-panel').forEach(p => p.hidden = p.id !== `tab-${name}`);
}

// ── 날씨 ───────────────────────────────────────────────
function showWeather(w) {
  state.weather = w;
  $('#wTemp').textContent = Math.round(w.temp);
  $('#wDesc').textContent = `${w.place ? w.place + ' · ' : ''}${w.desc}`;
  const feels = w.feels_like != null && w.feels_like !== w.temp ? ` · 체감 ${w.feels_like}°C` : '';
  const rain = w.rain_prob != null ? ` · 강수확률 ${w.rain_prob}%` : '';
  $('#wMeta').textContent = `습도 ${w.humidity}%${feels}${rain}`;
  $('#weatherMsg').textContent = '';
  $('#manualWeather').open = false;
}

async function loadWeather(query) {
  $('#weatherMsg').textContent = '날씨를 불러오는 중…';
  try { showWeather(await api(`/api/weather?${query}`)); }
  catch (e) {
    $('#weatherMsg').textContent = e.message;
    $('#manualWeather').open = true;
    state.weather = null;
  }
}

function currentWeather() {
  if (state.weather && !$('#manualWeather').open) return state.weather;
  return { temp: Number($('#mTemp').value || 15), desc: $('#mDesc').value, humidity: Number($('#mHum').value || 50) };
}

function setupWeather() {
  $('#citySearchBtn').onclick = () => {
    const city = $('#cityInput').value.trim();
    if (city) loadWeather(`city=${encodeURIComponent(city)}`);
  };
  $('#cityInput').onkeydown = e => { if (e.key === 'Enter') $('#citySearchBtn').click(); };
  $('#geoBtn').onclick = () => {
    if (!navigator.geolocation) { $('#weatherMsg').textContent = '이 브라우저는 위치를 지원하지 않아요.'; return; }
    $('#weatherMsg').textContent = '위치를 확인하는 중…';
    navigator.geolocation.getCurrentPosition(
      p => loadWeather(`lat=${p.coords.latitude}&lon=${p.coords.longitude}`),
      () => { $('#weatherMsg').textContent = '위치 권한이 없어요. 도시를 검색하거나 직접 입력해 주세요.'; },
      { timeout: 8000 });
  };
  api('/api/weather/cities').then(list => {
    $('#cityList').innerHTML = list.map(c => `<option value="${esc(c)}">`).join('');
  }).catch(() => {});
  $('#manualWeather').ontoggle = () => {
    if ($('#manualWeather').open && state.weather) $('#mTemp').value = Math.round(state.weather.temp);
  };
}

// ── 추천 ───────────────────────────────────────────────
function itemCard(item, { label, actions = '' } = {}) {
  const photo = item.photo_url
    ? `<img src="${esc(item.photo_url)}" alt="${esc(item.name || item.category)}" loading="lazy">`
    : CATEGORY_EMOJI[item.category] || '👚';
  const sub = [item.color, item.season, item.style].filter(Boolean).join(' · ');
  return `<div class="item">
    <div class="item-photo">${photo}</div>
    ${actions}
    <div class="item-info"><b>${esc(label || item.name || item.category)}</b><span class="muted">${esc(label && item.name ? item.name + ' · ' + sub : sub)}</span></div>
  </div>`;
}

function setResult(mode) {
  $('#resultEmpty').hidden = mode !== 'empty';
  $('#resultLoading').hidden = mode !== 'loading';
  $('#resultBody').hidden = mode !== 'body';
}

async function recommend() {
  const btn = $('#recommendBtn');
  $('#resultError').textContent = '';
  $('#wearMsg').textContent = '';
  btn.disabled = true;
  setResult('loading');
  if (window.innerWidth <= 820) $('#resultCard').scrollIntoView({ behavior: 'smooth' });
  try {
    const data = await api('/api/recommend', {
      method: 'POST',
      body: { weather: currentWeather(), mood: state.mood, occasions: [...state.occasions], style: state.style },
    });
    state.lastPicks = data.picks;
    $('#pickGrid').innerHTML = data.picks.map(p => itemCard(p.item, { label: p.slot === 'accessory' ? p.item.category : SLOT_LABEL[p.slot] })).join('');
    $('#pickGrid').hidden = data.picks.length === 0;
    $('#answerText').innerHTML = formatAnswer(data.text);
    const byId = Object.fromEntries(state.items.map(i => [i.id, i]));
    $('#candidateList').innerHTML = data.candidates.map(c => {
      const it = byId[c.item_id];
      return it ? `<li>${esc(it.category)} 1순위: ${esc(it.name || it.color || '이름 없음')} — ${c.score}점 (${esc(c.reason)})</li>` : '';
    }).join('');
    $('.why').hidden = !data.used_wardrobe;
    $('#wearBtn').hidden = data.picks.length === 0;
    $('#wearBtn').disabled = false;
    setResult('body');
  } catch (e) {
    setResult('empty');
    $('#resultError').textContent = e.message;
  } finally { btn.disabled = false; }
}

async function wearToday() {
  try {
    await api('/api/wardrobe/wear', { method: 'POST', body: { item_ids: state.lastPicks.map(p => p.item.id) } });
    $('#wearMsg').textContent = '오늘의 코디로 기록했어요. 다음 추천에서는 방금 입은 옷이 덜 나와요.';
    $('#wearBtn').disabled = true;
    await loadItems();
  } catch (e) { $('#wearMsg').textContent = e.message; }
}

function setupToday() {
  renderChips($('#moodChips'), OPTIONS.mood, { selected: () => state.mood, onChange: v => state.mood = v, labels: MOOD_EMOJI });
  renderChips($('#occasionChips'), OPTIONS.occasion, { selected: () => state.occasions, onChange: s => state.occasions = s, max: 4 });
  renderChips($('#styleChips'), OPTIONS.style, { selected: () => state.style, onChange: v => state.style = v });
  $('#recommendBtn').onclick = recommend;
  $('#againBtn').onclick = recommend;
  $('#wearBtn').onclick = wearToday;
}

// ── 옷장 ───────────────────────────────────────────────
async function loadItems() {
  try { state.items = await api('/api/wardrobe'); } catch { state.items = []; }
  renderWardrobe();
}

function renderWardrobe() {
  const items = state.items;
  const usable = items.filter(i => i.category !== '미분류').length;
  $('#wardrobeCount').textContent = items.length ? items.length : '';
  $('#wardrobeNote').textContent = usable
    ? `내 옷장의 옷 ${usable}벌 중에서 골라 드려요.`
    : '옷장이 비어 있어서 일반적인 코디를 추천해요. 옷을 등록하면 내 옷으로 골라 드려요.';

  const unclassified = items.filter(i => i.category === '미분류').length;
  $('#unclassifiedHint').hidden = unclassified === 0;
  $('#unclassifiedHint').textContent = `🏷 미분류 ${unclassified}벌 — 카드의 카테고리 버튼을 한 번 눌러 정리해 주세요. 정리 전에는 추천에 쓰지 않아요.`;

  const filters = ['전체', ...OPTIONS.category, ...(unclassified ? ['미분류'] : [])];
  if (!filters.includes(state.filter)) state.filter = '전체';
  renderChips($('#filterChips'), filters, { selected: () => state.filter, onChange: v => { state.filter = v; renderWardrobe(); } });

  const shown = state.filter === '전체' ? items : items.filter(i => i.category === state.filter);
  if (shown.length === 0) {
    $('#itemGrid').innerHTML = `<div class="empty" style="grid-column:1/-1"><div class="empty-icon">👕</div>
      <p>${items.length ? '이 카테고리에는 옷이 없어요.' : '아직 등록된 옷이 없어요.'}</p>
      <p class="small">사진을 올리면 색상은 자동으로 채워져요.</p></div>`;
    return;
  }
  $('#itemGrid').innerHTML = shown.map(it => {
    const actions = `<div class="item-actions">
      <button class="icon-btn" data-edit="${it.id}" title="수정" aria-label="수정">✎</button>
      <button class="icon-btn" data-del="${it.id}" title="삭제" aria-label="삭제">✕</button></div>`;
    const card = itemCard(it, { actions });
    if (it.category !== '미분류') return card;
    const quick = `<div class="quick-cat">${OPTIONS.category.map(c => `<button type="button" data-quick="${it.id}" data-cat="${c}">${c}</button>`).join('')}</div>`;
    return card.replace('<div class="item-info">', quick + '<div class="item-info">');
  }).join('');
}

async function onGridClick(e) {
  const t = e.target.closest('button');
  if (!t) return;
  try {
    if (t.dataset.del) {
      if (!confirm('이 옷을 옷장에서 삭제할까요?')) return;
      await api(`/api/wardrobe/${t.dataset.del}`, { method: 'DELETE' });
    } else if (t.dataset.quick) {
      await api(`/api/wardrobe/${t.dataset.quick}`, { method: 'PATCH', body: { category: t.dataset.cat } });
    } else if (t.dataset.edit) {
      openItemDialog(state.items.find(i => i.id === Number(t.dataset.edit)));
      return;
    } else return;
    await loadItems();
  } catch (err) { alert(err.message); }
}

// 사진은 브라우저에서 긴 변 800px JPEG로 줄여 올린다 (업로드 용량과 서버 저장 공간 절약)
function resizeImage(file, maxDim = 800, quality = 0.85) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      const scale = Math.min(1, maxDim / Math.max(img.width, img.height));
      const canvas = document.createElement('canvas');
      canvas.width = Math.round(img.width * scale); canvas.height = Math.round(img.height * scale);
      canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height);
      URL.revokeObjectURL(url);
      canvas.toBlob(b => b ? resolve({ blob: b, canvas }) : reject(new Error('사진을 처리하지 못했어요.')), 'image/jpeg', quality);
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('사진을 열지 못했어요.')); };
    img.src = url;
  });
}

// 사진 가운데 부분의 평균 색을 가장 가까운 색 이름으로 바꾼다 (외부 API 없이 브라우저에서 계산)
const PALETTE = [['화이트', 248, 248, 246], ['블랙', 24, 24, 24], ['그레이', 140, 140, 140], ['베이지', 222, 201, 168],
  ['브라운', 122, 84, 52], ['네이비', 32, 42, 82], ['블루', 64, 112, 200], ['스카이블루', 150, 196, 232], ['그린', 70, 130, 82],
  ['카키', 132, 128, 84], ['레드', 190, 52, 52], ['핑크', 232, 156, 184], ['옐로우', 230, 202, 62], ['오렌지', 222, 132, 52], ['퍼플', 122, 82, 162]];
function dominantColor(canvas) {
  try {
    const s = document.createElement('canvas'); s.width = s.height = 32;
    const ctx = s.getContext('2d'); ctx.drawImage(canvas, 0, 0, 32, 32);
    const d = ctx.getImageData(8, 8, 16, 16).data;
    let r = 0, g = 0, b = 0;
    for (let i = 0; i < d.length; i += 4) { r += d[i]; g += d[i + 1]; b += d[i + 2]; }
    const n = d.length / 4; r /= n; g /= n; b /= n;
    return PALETTE.reduce((best, [name, pr, pg, pb]) => {
      const dist = (r - pr) ** 2 + (g - pg) ** 2 + (b - pb) ** 2;
      return dist < best.dist ? { name, dist } : best;
    }, { name: '', dist: Infinity }).name;
  } catch { return ''; }
}

function openItemDialog(item = null) {
  state.editingId = item ? item.id : null;
  state.photoBlob = null;
  state.itemFields = { category: item?.category ?? '상의', season: item?.season ?? '사계절', style: item?.style ?? '캐주얼' };
  const form = $('#itemForm');
  form.reset();
  form.querySelector('[data-error]').textContent = '';
  form.color.value = item?.color ?? '';
  form.name.value = item?.name ?? '';
  $('#itemDialogTitle').textContent = item ? '옷 정보 수정' : '옷 추가';
  $('#photoDrop').hidden = !!item;                     // 사진 교체는 삭제 후 다시 등록
  $('#photoPreview').hidden = true;
  $('#photoText').hidden = false;
  const cats = item?.category === '미분류' ? [...OPTIONS.category, '미분류'] : OPTIONS.category;
  [['category', cats], ['season', OPTIONS.season], ['style', OPTIONS.style]].forEach(([key, values]) =>
    renderChips(form.querySelector(`[data-item="${key}"]`), values,
      { selected: () => state.itemFields[key], onChange: v => state.itemFields[key] = v }));
  $('#itemDialog').showModal();
}

async function onPhotoPicked(e) {
  const file = e.target.files[0];
  if (!file) return;
  const form = $('#itemForm');
  try {
    const { blob, canvas } = await resizeImage(file);
    state.photoBlob = blob;
    $('#photoPreview').src = URL.createObjectURL(blob);
    $('#photoPreview').hidden = false;
    $('#photoText').hidden = true;
    if (!form.color.value) form.color.value = dominantColor(canvas);
  } catch (err) { form.querySelector('[data-error]').textContent = err.message; }
}

async function saveItem(e) {
  e.preventDefault();
  const form = $('#itemForm');
  const btn = $('#itemSaveBtn');
  btn.disabled = true;
  try {
    const fields = { ...state.itemFields, color: form.color.value.trim(), name: form.name.value.trim() };
    if (state.editingId) {
      await api(`/api/wardrobe/${state.editingId}`, { method: 'PATCH', body: fields });
    } else {
      const fd = new FormData();
      Object.entries(fields).forEach(([k, v]) => fd.append(k, v));
      if (state.photoBlob) fd.append('photo', state.photoBlob, 'photo.jpg');
      await api('/api/wardrobe', { method: 'POST', form: fd });
    }
    $('#itemDialog').close();
    await loadItems();
  } catch (err) { form.querySelector('[data-error]').textContent = err.message; }
  finally { btn.disabled = false; }
}

// 여러 장 한 번에: 카테고리는 '미분류'로 올려 두고 나중에 카드에서 한 번씩 눌러 정리한다
async function onBulkPicked(e) {
  const files = [...e.target.files].slice(0, 30);
  e.target.value = '';
  let ok = 0;
  for (const file of files) {
    try {
      const { blob, canvas } = await resizeImage(file);
      const fd = new FormData();
      fd.append('category', '미분류');
      fd.append('color', dominantColor(canvas));
      fd.append('photo', blob, 'photo.jpg');
      await api('/api/wardrobe', { method: 'POST', form: fd });
      ok++;
    } catch { /* 실패한 사진은 건너뛴다 */ }
  }
  state.filter = '미분류';
  await loadItems();
  if (ok < files.length) alert(`${files.length}장 중 ${ok}장을 올렸어요.`);
}

function setupWardrobe() {
  $('#itemGrid').onclick = onGridClick;
  $('#addItemBtn').onclick = () => openItemDialog();
  $('#photoInput').onchange = onPhotoPicked;
  $('#itemForm').onsubmit = saveItem;
  $('#itemCancelBtn').onclick = () => $('#itemDialog').close();
  $('#bulkInput').onchange = onBulkPicked;
}

// ── 프로필 ─────────────────────────────────────────────
const profileDraft = {};
function fillProfile() {
  const u = state.user;
  ['gender', 'body_type', 'skin_tone', 'personal_color'].forEach(key => {
    profileDraft[key] = u[key];
    renderChips($(`[data-field="${key}"]`), OPTIONS[key], { selected: () => profileDraft[key], onChange: v => profileDraft[key] = v });
  });
  const f = $('#profileForm');
  f.height_cm.value = u.height_cm ?? '';
  f.weight_kg.value = u.weight_kg ?? '';
}

async function saveProfile(e) {
  e.preventDefault();
  const f = e.target;
  const num = v => v === '' ? null : Number(v);
  try {
    state.user = await api('/api/me/profile', {
      method: 'PUT', body: { ...profileDraft, height_cm: num(f.height_cm.value), weight_kg: num(f.weight_kg.value) },
    });
    $('#profileMsg').textContent = '저장했어요. 다음 추천부터 반영돼요.';
  } catch (err) { $('#profileMsg').textContent = err.message; }
}

// ── 시작 ───────────────────────────────────────────────
async function init() {
  setupAuth(); setupWeather(); setupToday(); setupWardrobe();
  $$('.tab').forEach(t => t.onclick = () => showTab(t.dataset.tab));
  $('#profileForm').onsubmit = saveProfile;

  const saved = storage('get');
  if (!saved) return showAuth();
  state.token = saved;
  try { await enterApp(saved, await api('/api/me')); }
  catch { logout(); }
}

init();
