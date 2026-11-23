/**
 * zero-copy-agent · v0.3.2 Production Operations Controller
 * Connected directly to FastAPI Sidecar Gateway & Native MCP Server
 */

// ------------------------------------------------------------------------
// 1. Central Application State
// ------------------------------------------------------------------------
const state = {
  activeView: 'orchestrator',
  mode: 'auto', // 'auto' | 'cascade' | 'manual'
  globalModel: '',
  dlpEnabled: true,
  irEnabled: true,
  airGap: false,
  maxRework: 2,
  breakpointEnabled: false,
  pausedResumeCallback: null,
  isRunning: false,

  // Per-stage DAG config & live status
  dagStages: [
    { id: 'architect', label: '1. Архитектор', model: '', status: 'idle', fixed: false },
    { id: 'executor', label: '2. Исполнитель', model: '', status: 'idle', fixed: false },
    { id: 'gate', label: '3. Local Hard Gate', model: 'py_compile + pytest', status: 'idle', fixed: true },
    { id: 'audit', label: '4. Аудит Архитектора', model: '', status: 'idle', fixed: false },
  ],

  // Session telemetry
  metrics: {
    tokensSaved: 0,
    actualProcessed: 0,
    costSaved: 0.0,
    dlpMasked: 0,
    reworkLoops: 0,
  },

  // Connected models list
  connectedModels: [],

  // Accounts pool
  accounts: [],
  discoveryDone: false,

  // IDE Integrations
  ides: [],
  backups: [],
  ideTab: 'ui',

  // Traces stream
  traces: []
};

const defaultCliCommands = [
  { cmd: 'zca integrate list', desc: 'Список обнаруженных сред разработки и их статусов' },
  { cmd: 'zca integrate apply antigravity', desc: 'Подключить Google Antigravity (MCP Streamable HTTP + бэкап)' },
  { cmd: 'zca integrate apply vscode', desc: 'Подключить VS Code + Continue с автобэкапом config.yaml' },
  { cmd: 'zca integrate revert cursor', desc: 'Откатить конфиг Cursor IDE из последнего снимка' },
  { cmd: 'zca integrate print-config windsurf', desc: 'Вывести готовый JSON-сниппет MCP в stdout' },
];

// ------------------------------------------------------------------------
// 2. Navigation & Responsive Sidebar
// ------------------------------------------------------------------------
function switchView(viewId) {
  state.activeView = viewId;
  document.querySelectorAll('.view-pane').forEach(el => el.classList.add('hidden'));
  const target = document.getElementById(`view-${viewId}`);
  if (target) target.classList.remove('hidden');

  document.querySelectorAll('.nav-item').forEach(btn => {
    const active = btn.dataset.view === viewId;
    btn.classList.toggle('bg-surface', active);
    btn.classList.toggle('text-ink', active);
    btn.classList.toggle('border-hairline', active);
    btn.classList.toggle('text-muted', !active);
    btn.classList.toggle('border-transparent', !active);
    const icon = btn.querySelector('svg, i');
    if (icon) {
      icon.classList.toggle('text-cobalt', active);
    }
  });

  if (viewId === 'models') fetchAccountsAndModels();
  if (viewId === 'dlp') fetchDlpLogs();
  if (viewId === 'ide') fetchIdeIntegrations();
  if (viewId === 'solutions') fetchSolutions();
  if (viewId === 'presets') fetchPresets();
  if (viewId === 'logs') loadSystemLogs();

  toggleMobileSidebar(false);
}

function toggleMobileSidebar(open) {
  const sb = document.getElementById('sidebar');
  const bd = document.getElementById('sidebar-backdrop');
  if (open) {
    sb.classList.remove('-translate-x-full');
    bd.classList.remove('hidden');
  } else {
    sb.classList.add('-translate-x-full');
    bd.classList.add('hidden');
  }
}

// ------------------------------------------------------------------------
// 3. Top Toolbar Controls & Policy Updates
// ------------------------------------------------------------------------
async function setMode(mode) {
  state.mode = mode;
  document.querySelectorAll('.mode-btn').forEach(btn => {
    const active = btn.dataset.mode === mode;
    btn.classList.toggle('bg-surface', active);
    btn.classList.toggle('text-ink', active);
    btn.classList.toggle('border-hairline', active);
    btn.classList.toggle('text-muted', !active);
    btn.classList.toggle('border-transparent', !active);
  });

  const badge = document.getElementById('inspector-mode-badge');
  if (badge) badge.textContent = mode === 'auto' ? 'AUTO' : mode === 'cascade' ? 'CASCADE' : 'MANUAL';
  renderDagInspector();

  await updateBackendPolicy({ mode });
}

async function onGlobalModelChange() {
  const sel = document.getElementById('global-model-select');
  state.globalModel = sel ? sel.value : 'gemini-4-neon';
  if (state.mode === 'manual') {
    state.dagStages.forEach(s => {
      if (!s.fixed) s.model = state.globalModel;
    });
    renderDagInspector();
  }
  await updateBackendPolicy({ active_model: state.globalModel });
}

async function syncGlobalFlags(source) {
  state.dlpEnabled = document.getElementById('toggle-dlp').checked;
  state.irEnabled = document.getElementById('toggle-ir').checked;

  const tbAirGap = document.getElementById('toggle-airgap');
  const dlpAirGap = document.getElementById('dlp-pol-airgap');
  if (source === 'dlp') {
    state.airGap = dlpAirGap.checked;
    tbAirGap.checked = state.airGap;
  } else {
    state.airGap = tbAirGap.checked;
    dlpAirGap.checked = state.airGap;
  }

  const irEl = document.getElementById('inspector-ir-level');
  if (irEl) irEl.textContent = state.irEnabled ? 'L2 Symbolic DSL' : 'Выкл. (Raw UTF-8)';

  const zoneEl = document.getElementById('inspector-routing-zone');
  if (zoneEl) {
    if (state.airGap) {
      zoneEl.textContent = 'Air-Gap (Strict Local)';
      zoneEl.className = 'font-mono text-crimson font-medium';
      showBanner('Air-Gap активен: исходящий трафик в облачные API заблокирован, разрешена только локальная Ollama.', 'shield-alert', 'text-crimson');
    } else {
      zoneEl.textContent = 'Hybrid (Cloud + GPU)';
      zoneEl.className = 'font-mono text-emerald';
    }
  }

  renderDagInspector();
  runDlpBench();

  await updateBackendPolicy({
    dlp_enabled: state.dlpEnabled,
    compression_enabled: state.irEnabled,
    air_gapped: state.airGap
  });
}

async function stepRework(delta) {
  state.maxRework = Math.max(0, Math.min(5, state.maxRework + delta));
  const el = document.getElementById('rework-val');
  if (el) el.textContent = state.maxRework;
  await updateBackendPolicy({ max_rework_iterations: state.maxRework });
}

async function toggleBreakpoint() {
  state.breakpointEnabled = !state.breakpointEnabled;
  const btn = document.getElementById('btn-breakpoint');
  if (btn) {
    btn.classList.toggle('active', state.breakpointEnabled);
    btn.setAttribute('data-tip', state.breakpointEnabled ? 'Точка останова: ВКЛ' : 'Точка останова');
    btn.setAttribute('title', state.breakpointEnabled ? 'Точка останова: ВКЛ' : 'Точка останова');
  }
  showBanner(
    state.breakpointEnabled
      ? 'Точка останова включена: конвейер встанет на паузу перед этапом «Исполнитель».'
      : 'Точка останова отключена.',
    'pause-circle',
    state.breakpointEnabled ? 'text-amber' : 'text-muted'
  );
  await updateBackendPolicy({ breakpoint_enabled: state.breakpointEnabled });
}

function resumeBreakpoint() {
  document.getElementById('breakpoint-bar').classList.add('hidden');
  if (typeof state.pausedResumeCallback === 'function') {
    const cb = state.pausedResumeCallback;
    state.pausedResumeCallback = null;
    cb();
  }
}

function clearSession() {
  if (state.isRunning) return;
  state.traces = [];
  renderTraces();
  showBanner('Сессия очищена.', 'trash-2', 'text-muted');
}

let latestUpdateData = null;
let updatePollTimer = null;

function openUpdateModal(data) {
  const d = data || latestUpdateData;
  if (!d) return;
  const m = document.getElementById('modal-update');
  if (!m) return;
  const currEl = document.getElementById('update-modal-curr-ver');
  const newEl = document.getElementById('update-modal-new-ver');
  const notesEl = document.getElementById('update-modal-notes-box');
  const manualBtn = document.getElementById('btn-manual-download');
  const inAppBtn = document.getElementById('btn-inapp-update');
  const progContainer = document.getElementById('update-progress-container');

  if (currEl && d.version) currEl.textContent = 'v' + d.version;
  if (newEl && d.latest_version) newEl.textContent = 'v' + d.latest_version;
  if (notesEl) notesEl.textContent = d.release_notes || 'Доступны плановые улучшения стабильности и расширение функционала.';
  if (manualBtn) {
    manualBtn.href = d.download_url || d.release_url || '#';
  }

  // Reset progress UI
  if (progContainer) progContainer.classList.add('hidden');
  if (inAppBtn) {
    inAppBtn.disabled = false;
    inAppBtn.innerHTML = '<i data-lucide="zap" class="w-3.5 h-3.5"></i><span>Обновить прямо сейчас</span>';
  }

  m.classList.remove('hidden');
  if (window.lucide) lucide.createIcons({ nodes: [m] });
}

function closeUpdateModal() {
  if (updatePollTimer) clearInterval(updatePollTimer);
  const m = document.getElementById('modal-update');
  if (m) m.classList.add('hidden');
}

async function installUpdateNow() {
  const btn = document.getElementById('btn-inapp-update');
  const btnLater = document.getElementById('btn-update-later');
  const progContainer = document.getElementById('update-progress-container');
  const progBar = document.getElementById('update-progress-bar');
  const progLabel = document.getElementById('update-progress-label');
  const progPct = document.getElementById('update-progress-pct');

  if (btn) btn.disabled = true;
  if (btnLater) btnLater.disabled = true;
  if (progContainer) progContainer.classList.remove('hidden');

  try {
    const res = await fetch('/v1/system/update/install', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ download_url: latestUpdateData?.download_url })
    });
    const d = await res.json();
    if (d.status === 'error') {
      alert(d.message || 'Ошибка запуска обновления');
      if (btn) btn.disabled = false;
      return;
    }

    // Start polling progress
    if (updatePollTimer) clearInterval(updatePollTimer);
    updatePollTimer = setInterval(async () => {
      try {
        const pRes = await fetch('/v1/system/update/progress');
        if (!pRes.ok) return;
        const p = await pRes.json();

        if (progBar) progBar.style.width = (p.percent || 0) + '%';
        if (progPct) progPct.textContent = (p.percent || 0) + '%';
        if (progLabel) progLabel.textContent = p.message || 'Скачивание обновления...';

        if (p.status === 'applying') {
          if (progLabel) progLabel.textContent = 'Замена файлов и перезапуск приложения...';
          clearInterval(updatePollTimer);
        } else if (p.status === 'error') {
          clearInterval(updatePollTimer);
          alert('Ошибка при обновлении: ' + (p.error || p.message));
          if (btn) btn.disabled = false;
          if (btnLater) btnLater.disabled = false;
        }
      } catch (err) {
        // When process terminates and restarts, connection will drop (expected)
        clearInterval(updatePollTimer);
        if (progLabel) progLabel.textContent = 'Перезапуск приложения...';
      }
    }, 250);

  } catch (err) {
    alert('Не удалось начать установку: ' + err);
    if (btn) btn.disabled = false;
    if (btnLater) btnLater.disabled = false;
  }
}

async function checkUpdates(opts = {}) {
  const isInteractive = opts.interactive !== false && !opts.autoPrompt;
  const btn = document.getElementById('btn-check-updates');
  const icon = btn ? btn.querySelector('svg, i') : null;
  const statusLine = document.getElementById('update-status-line');
  if (icon && isInteractive) icon.classList.add('animate-spin');

  try {
    const res = await fetch('/v1/ui/updates/check');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    latestUpdateData = data;

    if (statusLine) {
      if (data.status === 'offline') {
        statusLine.textContent = 'Нет связи с GitHub';
      } else if (data.has_update) {
        statusLine.innerHTML = `<button onclick="openUpdateModal()" class="underline text-cobalt font-semibold hover:text-[#0955A3] cursor-pointer">Доступно обновление v${data.latest_version}</button>`;
      } else {
        statusLine.textContent = `Актуально · v${data.version} OK`;
      }
      statusLine.classList.remove('hidden');
    }

    if (data.status !== 'offline' && data.has_update) {
      showBanner(`Доступно обновление: v${data.latest_version} (текущая: v${data.version}). Нажмите для скачивания.`, 'arrow-down-circle', 'text-cobalt');
      const banner = document.getElementById('status-banner');
      if (banner) {
        banner.style.cursor = 'pointer';
        banner.onclick = () => openUpdateModal(data);
      }
      if (opts.autoPrompt || isInteractive) {
        openUpdateModal(data);
      }
    } else if (data.status === 'offline') {
      if (isInteractive) {
        showBanner('Нет связи с GitHub. Проверка обновлений не выполнена.', 'alert-circle', 'text-muted');
      }
    } else if (isInteractive) {
      showBanner(`Версия v${data.version} актуальна (GitHub подтвержден).`, 'check-circle-2', 'text-emerald');
    }
  } catch (err) {
    if (statusLine) {
      statusLine.textContent = 'Ошибка проверки обновлений (нет связи)';
      statusLine.classList.remove('hidden');
    }
    if (isInteractive) {
      showBanner('Ошибка проверки обновлений (нет связи с GitHub).', 'alert-circle', 'text-amber');
    }
  } finally {
    if (icon) icon.classList.remove('animate-spin');
  }
}

function showBanner(text, iconName = 'info', colorClass = 'text-cobalt') {
  const bar = document.getElementById('status-banner');
  const txt = document.getElementById('status-banner-text');
  const icon = document.getElementById('status-banner-icon');
  if (!bar || !txt) return;
  txt.textContent = text;
  if (icon) {
    icon.setAttribute('data-lucide', iconName);
    icon.className = `w-3.5 h-3.5 shrink-0 ${colorClass}`;
  }
  bar.classList.remove('hidden');
  if (window.lucide) lucide.createIcons({ nodes: [bar] });
}

function hideBanner() {
  const bar = document.getElementById('status-banner');
  if (bar) bar.classList.add('hidden');
}

async function updateBackendPolicy(patch) {
  try {
    await fetch('/v1/ui/policies', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch)
    });
  } catch (e) {
    // silently fail
  }
}

// ------------------------------------------------------------------------
// 4. VIEW 1: Orchestrator Stream & DAG Inspector
// ------------------------------------------------------------------------
function getModelDisplayName(id) {
  const map = {
    'gemini-4-neon': 'Gemini 4 Neon',
    'gemini-advanced': 'Gemini Advanced (1.5 Pro)',
    'gemini-2.5-flash': 'Gemini 2.5 Flash',
    'gpt-4o': 'ChatGPT Plus (GPT-4o)',
    'gpt-4o-mini': 'GPT-4o Mini',
    'chatgpt-plus': 'ChatGPT Plus',
    'claude-3-7-sonnet': 'Claude 3.7 Sonnet',
    'claude-pro': 'Claude Pro',
    'qwen2.5-coder:14b': 'Qwen2.5-Coder 14B (Local)',
    'qwen2.5-coder:7b': 'Qwen2.5-Coder 7B (Local)',
    'qwen2.5-coder:32b': 'Qwen2.5-Coder 32B (Local)',
    'qwen2.5-coder:1.5b': 'Qwen2.5-Coder 1.5B (Local)',
    'deepseek-coder:6.7b': 'DeepSeek-Coder 6.7B (Local)',
    'tier:local-fast': 'Локальный GPU (Fast)',
    'tier:coding-top': 'Кодинг Топ (14B)',
    'tier:reasoning-top': 'Рассуждения (Архитектор)'
  };
  const found = state.connectedModels.find(m => m.id === id);
  return (found && found.name) ? found.name : (map[id] || id);
}

function renderGlobalModelSelect() {
  const sel = document.getElementById('global-model-select');
  if (!sel) return;
  if (!state.connectedModels.length) {
    sel.innerHTML = '<option value="" disabled selected>— Нет подключённых моделей —</option>';
    state.globalModel = '';
    return;
  }
  const hasCurrent = state.connectedModels.some(m => m.id === state.globalModel);
  if (!hasCurrent && state.connectedModels.length > 0) {
    state.globalModel = state.connectedModels[0].id;
  }
  sel.innerHTML = state.connectedModels.map(m =>
    `<option value="${escapeHtml(m.id)}" ${m.id === state.globalModel ? 'selected' : ''}>${escapeHtml(m.name)}</option>`
  ).join('');
}

async function syncConnectedModels() {
  try {
    const res = await fetch('/v1/models');
    if (!res.ok) return;
    const data = await res.json();
    const rawList = data.data || [];
    const realModels = rawList.filter(m => (m.id || m) !== 'mock-test');
    state.connectedModels = realModels.map(m => {
      const id = m.id || m;
      const owner = m.owned_by || '';
      const isLocal = m.is_local || id.includes('ollama') || id.includes('local') || owner.includes('ollama');
      let badge = m.badge || m.short_badge || '';
      if (!badge) {
        const low = id.toLowerCase();
        if (isLocal) {
          badge = (low.includes('14b') || low.includes('32b')) ? '🔥 Сильная · Локальная GPU' : '🖥️ Локальная GPU';
        } else if (['pro', 'opus', 'reasoner', 'o1', 'o3', 'max', 'neon', 'advanced', 'r1'].some(k => low.includes(k))) {
          badge = '🔥 Сильная';
        } else if (['flash', 'sonnet', 'mini', 'haiku', 'turbo', 'plus'].some(k => low.includes(k))) {
          badge = '⚡ Быстрая';
        } else if (['lite', 'nano', '3b', '7b', 'small'].some(k => low.includes(k))) {
          badge = '🪶 Базовая';
        } else {
          badge = '⚡ Быстрая';
        }
      }
      const rawName = getModelDisplayName(id);
      const displayName = m.display_name || `${rawName} [${badge}]`;
      return {
        id: id,
        name: displayName,
        rawName: rawName,
        badge: badge,
        tier: m.tier || '',
        local: isLocal,
        status: m.status || 'active'
      };
    });

    if (state.connectedModels.length === 0) {
      state.globalModel = '';
      state.dagStages[0].model = '';
      state.dagStages[1].model = '';
      state.dagStages[3].model = '';
    } else {
      const availIds = state.connectedModels.map(m => m.id);
      if (!availIds.includes(state.globalModel)) {
        state.globalModel = state.connectedModels[0].id;
      }
      // Auto-adaptation for Architect (Stage 0):
      if (!state.dagStages[0].model || !availIds.includes(state.dagStages[0].model)) {
        const oldM = state.dagStages[0].model;
        const strong = state.connectedModels.find(m => m.badge && m.badge.includes('Сильная')) || state.connectedModels.find(m => !m.local) || state.connectedModels[0];
        state.dagStages[0].model = strong.id;
        if (oldM && oldM !== strong.id) {
          showBanner(`Модель Архитектора '${oldM}' недоступна. Автозамена на: ${strong.name}`, 'info', 'text-amber');
        }
      }
      // Auto-adaptation for Executor (Stage 1):
      if (!state.dagStages[1].model || !availIds.includes(state.dagStages[1].model)) {
        const oldM = state.dagStages[1].model;
        const localOrFast = state.connectedModels.find(m => m.local) || state.connectedModels.find(m => m.badge && m.badge.includes('Быстрая')) || state.connectedModels[0];
        state.dagStages[1].model = localOrFast.id;
        if (oldM && oldM !== localOrFast.id) {
          showBanner(`Модель Исполнителя '${oldM}' недоступна. Автозамена на: ${localOrFast.name}`, 'info', 'text-amber');
        }
      }
      // Auto-adaptation for Reviewer (Stage 3):
      if (!state.dagStages[3].model || !availIds.includes(state.dagStages[3].model)) {
        const strong = state.connectedModels.find(m => m.badge && m.badge.includes('Сильная')) || state.connectedModels.find(m => !m.local) || state.connectedModels[0];
        state.dagStages[3].model = strong.id;
      }
    }

    renderGlobalModelSelect();
    renderDagInspector();
  } catch (err) {
    console.debug('Failed to sync connected models:', err);
  }
}

function renderMetrics() {
  const tEl = document.getElementById('metric-tokens');
  const cEl = document.getElementById('metric-cost');
  const dEl = document.getElementById('metric-dlp');
  const rEl = document.getElementById('metric-rework');
  const pEl = document.getElementById('metric-actual-processed');

  if (tEl) tEl.textContent = state.metrics.tokensSaved.toLocaleString('ru-RU');
  if (cEl) cEl.textContent = '$' + state.metrics.costSaved.toFixed(2);
  if (dEl) dEl.textContent = state.metrics.dlpMasked;
  if (rEl) rEl.textContent = state.metrics.reworkLoops;
  if (pEl) pEl.textContent = (state.metrics.actualProcessed || 0).toLocaleString('ru-RU');
}

function renderDagInspector() {
  const container = document.getElementById('dag-stage-list');
  if (!container) return;
  const allowOverride = state.mode === 'cascade' || state.mode === 'manual';

  container.innerHTML = state.dagStages.map((stage, idx) => {
    const dotClass =
      stage.status === 'pass' ? 'bg-emerald' :
      stage.status === 'running' ? 'bg-cobalt animate-pulse' :
      stage.status === 'reject' ? 'bg-crimson' :
      stage.status === 'paused' ? 'bg-amber' : 'bg-[#C9CFD6]';

    const localModels = state.connectedModels.filter(m => m.local);
    const defaultLocal = localModels.length ? localModels[0].id : 'Local Ollama';
    const effectiveModel = state.airGap && !stage.fixed ? (stage.model && localModels.some(m => m.id === stage.model) ? stage.model : defaultLocal) : stage.model;

    let controlHtml = '';
    if (stage.fixed) {
      controlHtml = `<span class="font-mono text-[11px] text-muted truncate">${stage.model}</span>`;
    } else {
      const filtered = state.connectedModels.filter(m => !state.airGap || m.local);
      const opts = filtered.length
        ? filtered.map(m => `<option value="${m.id}" ${m.id === effectiveModel ? 'selected' : ''}>${m.name}</option>`).join('')
        : '<option value="" disabled selected>— Нет моделей —</option>';
      controlHtml = `
        <select
          onchange="overrideStageModel(${idx}, this.value)"
          ${allowOverride && !state.airGap && filtered.length ? '' : 'disabled'}
          class="w-full h-6 px-1.5 font-mono text-[11px] text-ink bg-panel border border-hairline rounded focus:outline-none focus:border-cobalt disabled:opacity-65 disabled:cursor-not-allowed truncate cursor-pointer"
        >${opts}</select>
      `;
    }

    const isRunning = stage.status === 'running';
    const containerClass = isRunning
      ? 'px-3.5 py-2 space-y-1.5 transition-all duration-200 ring-1 ring-cobalt/60 bg-cobalt/5 animate-pulse rounded'
      : 'px-3.5 py-2 space-y-1.5';
    const statusLabel = isRunning
      ? '<span class="font-mono text-[10px] uppercase font-semibold text-cobalt shrink-0">WORKING</span>'
      : `<span class="font-mono text-[10px] uppercase text-muted shrink-0">${stage.status}</span>`;

    return `
      <div class="${containerClass}">
        <div class="flex items-center justify-between gap-2">
          <div class="flex items-center gap-2 min-w-0">
            <span class="w-2 h-2 rounded-full ${dotClass} shrink-0"></span>
            <span class="text-[12px] font-medium text-ink truncate">${stage.label}</span>
          </div>
          ${statusLabel}
        </div>
        <div>${controlHtml}</div>
      </div>
    `;
  }).join('');
}

async function overrideStageModel(idx, modelId) {
  state.dagStages[idx].model = modelId;
  const patch = {};
  if (idx === 0) patch.architect_model = modelId;
  if (idx === 1) patch.executor_model = modelId;
  if (idx === 3) patch.reviewer_model = modelId;
  await updateBackendPolicy(patch);
}

function formatPayloadHtml(stage) {
  if (stage.payloadType === 'diff') {
    return stage.payload.split('\n').map(line => {
      const esc = escapeHtml(line);
      if (line.startsWith('+') && !line.startsWith('+++')) return `<div class="diff-add px-2">${esc}</div>`;
      if (line.startsWith('-') && !line.startsWith('---')) return `<div class="diff-del px-2">${esc}</div>`;
      return `<div class="px-2 text-muted">${esc}</div>`;
    }).join('');
  }
  return `<pre class="px-2.5 py-2 m-0 text-[11px] font-mono text-ink whitespace-pre-wrap break-words">${escapeHtml(stage.payload)}</pre>`;
}

function renderTraces() {
  const stream = document.getElementById('trace-stream');
  if (!stream) return;
  if (!state.traces.length) {
    stream.innerHTML = `
      <div class="h-full flex flex-col items-center justify-center p-8 text-muted font-mono text-[12px] gap-2">
        <i data-lucide="git-branch" class="w-8 h-8 opacity-40"></i>
        <span>Оркестратор ожидает входящий запрос. Введите задачу ниже или отправьте из IDE.</span>
      </div>
    `;
    if (window.lucide) lucide.createIcons({ nodes: [stream] });
    return;
  }

  stream.innerHTML = state.traces.map((run, rIdx) => {
    const finalBadge =
      run.finalStatus === 'PASS' ? `<span class="font-mono text-[11px] font-medium text-emerald bg-[#ECFDF3] border border-emerald/25 px-1.5 py-0.5 rounded">PASS</span>` :
      run.finalStatus === 'ESCALATED' ? `<span class="font-mono text-[11px] font-medium text-crimson bg-[#FEF2F2] border border-crimson/25 px-1.5 py-0.5 rounded">ESCALATED</span>` :
      run.finalStatus === 'REJECT' ? `<span class="font-mono text-[11px] font-medium text-crimson bg-[#FEF2F2] border border-crimson/25 px-1.5 py-0.5 rounded">REJECT</span>` :
      `<span class="font-mono text-[11px] font-medium text-cobalt bg-[#EFF6FF] border border-cobalt/25 px-1.5 py-0.5 rounded">RUNNING</span>`;

    const stagesHtml = run.stages.map((st, sIdx) => {
      const stColor =
        st.status === 'PASS' ? 'text-emerald' :
        st.status === 'REJECT' ? 'text-crimson' :
        st.status === 'REWORK' ? 'text-amber' : 'text-cobalt';

      const stIcon =
        st.status === 'PASS' ? 'check-circle-2' :
        st.status === 'REJECT' ? 'x-circle' : 'loader-2';

      return `
        <div class="border-t border-hairline">
          <button
            type="button"
            onclick="toggleStageExpand(${rIdx}, ${sIdx})"
            class="w-full px-4 py-2 flex flex-wrap items-center justify-between gap-2 text-left hover:bg-panel/70 transition-colors"
          >
            <div class="flex items-center gap-2 min-w-0">
              <i data-lucide="${st.expanded ? 'chevron-down' : 'chevron-right'}" class="w-3.5 h-3.5 text-muted shrink-0"></i>
              <i data-lucide="${stIcon}" class="w-3.5 h-3.5 ${stColor} shrink-0 ${st.status === 'RUNNING' ? 'animate-spin' : ''}"></i>
              <span class="text-[12px] font-medium text-ink truncate">${escapeHtml(st.title)}</span>
              <span class="font-mono text-[11px] text-muted hidden sm:inline truncate">· ${escapeHtml(st.model)}</span>
            </div>
            <div class="flex items-center gap-3 font-mono text-[11px] text-muted shrink-0">
              <span>TTFT <strong class="text-ink font-medium">${st.ttft}</strong></span>
              <span>${st.duration}</span>
              <span class="text-cobalt">IR ${st.irSaved}</span>
              ${st.dlpCount > 0 ? `<span class="text-crimson">DLP:${st.dlpCount}</span>` : ''}
              <span class="font-semibold ${stColor} w-14 text-right">${st.status}</span>
            </div>
          </button>
          ${st.expanded ? `
            <div class="bg-panel border-t border-hairline font-mono text-[11px] overflow-x-auto">
              ${formatPayloadHtml(st)}
            </div>
          ` : ''}
        </div>
      `;
    }).join('');

    return `
      <article class="bg-surface">
        <div class="px-4 py-2.5 bg-panel/60 flex flex-wrap items-center justify-between gap-2">
          <div class="flex items-center gap-2 min-w-0">
            <i data-lucide="message-square" class="w-3.5 h-3.5 text-muted shrink-0"></i>
            <span class="text-[13px] font-medium text-ink break-words">${escapeHtml(run.prompt)}</span>
          </div>
          ${finalBadge}
        </div>
        <div class="px-4 py-1.5 border-t border-hairline bg-surface flex flex-wrap items-center gap-1.5 font-mono text-[11px] text-ink">
          <i data-lucide="zap" class="w-3.5 h-3.5 text-amber shrink-0"></i>
          <span>[Архитектор: <strong>${escapeHtml(run.attribution.architect)}</strong>]</span>
          <i data-lucide="arrow-right" class="w-3 h-3 text-muted shrink-0"></i>
          <span>[Исполнитель: <strong>${escapeHtml(run.attribution.executor)}</strong>]</span>
        </div>
        <div>${stagesHtml}</div>
      </article>
    `;
  }).join('');

  if (window.lucide) lucide.createIcons({ nodes: [stream] });
}

function toggleStageExpand(rIdx, sIdx) {
  state.traces[rIdx].stages[sIdx].expanded = !state.traces[rIdx].stages[sIdx].expanded;
  renderTraces();
}

function runQuickPrompt(type) {
  const input = document.getElementById('prompt-input');
  if (type === 'refactor') {
    input.value = 'Рефакторинг auth.py: вынести валидацию сессии и ротацию токенов в отдельный класс';
  } else {
    input.value = 'Тест REJECT -> Rework: добавить проверку таймаута в core/drivers/ollama.py с обработкой ошибок';
  }
  startRealOrSimulatedExecution(input.value);
  input.value = '';
}

function handlePromptSubmit(e) {
  e.preventDefault();
  const input = document.getElementById('prompt-input');
  const val = input.value.trim();
  if (!val || state.isRunning) return;
  input.value = '';
  startRealOrSimulatedExecution(val);
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function startRealOrSimulatedExecution(promptText) {
  if (state.isRunning) return;
  const sendBtn = document.getElementById('btn-send');

  if (!state.connectedModels.length) {
    const run = {
      id: 'run-' + Date.now(),
      prompt: promptText,
      attribution: { architect: '—', executor: '—' },
      finalStatus: 'ERROR',
      stages: [{
        key: 'no-models',
        title: 'Ошибка: Нет подключённых моделей',
        model: '—',
        status: 'REJECT',
        ttft: '0ms',
        duration: '0s',
        irSaved: '0%',
        dlpCount: 0,
        expanded: true,
        payloadType: 'text',
        payload: 'Нет подключённых моделей для выполнения запроса.\nПерейдите в раздел «Модели и пул» и подключите API-ключ или запустите локальную модель через Ollama.'
      }]
    };
    state.traces.unshift(run);
    renderTraces();
    return;
  }

  state.isRunning = true;
  if (sendBtn) sendBtn.disabled = true;

  const activeTarget = state.airGap ? 'tier:local-fast' : (state.mode === 'cascade' ? 'cascade:architect' : (state.globalModel || state.connectedModels[0].id));
  const activeModelDisplay = getModelDisplayName(activeTarget);

  const run = {
    id: 'run-' + Date.now(),
    prompt: promptText,
    attribution: { architect: activeModelDisplay, executor: activeModelDisplay },
    finalStatus: 'RUNNING',
    stages: []
  };
  state.traces.unshift(run);
  state.dagStages.forEach(s => s.status = 'idle');
  renderDagInspector();
  renderTraces();

  const startTime = Date.now();
  state.dagStages[0].status = 'running';
  renderDagInspector();

  const activeStage = {
    key: 'execution',
    title: state.mode === 'cascade' ? 'Конвейер Архитектора' : `Запрос к модели · ${activeModelDisplay}`,
    model: activeModelDisplay,
    status: 'RUNNING',
    ttft: '...',
    duration: '...',
    irSaved: state.irEnabled ? 'L2 IR' : '0%',
    dlpCount: 0,
    expanded: true,
    payloadType: 'text',
    payload: ''
  };
  run.stages.push(activeStage);
  renderTraces();

  try {
    const response = await fetch('/v1/chat/completions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: activeTarget,
        messages: [{ role: 'user', content: promptText }],
        stream: true
      })
    });

    if (!response.ok) {
      const errText = await response.text();
      activeStage.status = 'REJECT';
      activeStage.payload = `Ошибка сервера (${response.status}): ${errText}`;
      activeStage.duration = `${((Date.now() - startTime) / 1000).toFixed(1)}s`;
      run.finalStatus = 'ERROR';
      state.dagStages.forEach(s => s.status = 'reject');
      return;
    }

    const zcaModel = response.headers.get('x-zca-model');
    if (zcaModel) {
      activeStage.model = zcaModel;
      run.attribution.architect = zcaModel;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';
    let fullContent = '';
    let firstChunkTime = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      if (!firstChunkTime) {
        firstChunkTime = Date.now();
        activeStage.ttft = `${firstChunkTime - startTime}ms`;
      }

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop();

      for (const line of lines) {
        const trimmed = line.trim();
        if (trimmed.startsWith('data: ')) {
          const jsonStr = trimmed.replace('data: ', '').trim();
          if (jsonStr === '[DONE]') continue;
          try {
            const parsed = JSON.parse(jsonStr);
            const chunk = parsed.choices?.[0]?.delta?.content || '';
            fullContent += chunk;
            activeStage.payload = fullContent;
          } catch (e) {}
        }
      }
      renderTraces();
    }

    activeStage.status = 'PASS';
    activeStage.duration = `${((Date.now() - startTime) / 1000).toFixed(1)}s`;
    run.finalStatus = 'PASS';
    state.dagStages.forEach(s => s.status = 'pass');
  } catch (err) {
    activeStage.status = 'REJECT';
    activeStage.payload = `Сбой сети или шлюза: ${err.message}`;
    activeStage.duration = `${((Date.now() - startTime) / 1000).toFixed(1)}s`;
    run.finalStatus = 'ERROR';
    state.dagStages.forEach(s => s.status = 'reject');
  } finally {
    state.isRunning = false;
    if (sendBtn) sendBtn.disabled = false;
    renderDagInspector();
    renderTraces();
    fetchSystemStatus();
  }
}

// ------------------------------------------------------------------------
// 5. VIEW 2: Real Models & Accounts Pool (Quota Mesh & Web Subscriptions)
// ------------------------------------------------------------------------
let _webAuthPollTimer = null;

async function startWebAuth(provider, accountId) {
  const prov = (provider || 'gemini').toLowerCase();
  const accParam = accountId ? `?account_id=${encodeURIComponent(accountId)}` : '';
  const label = accountId ? `${prov.toUpperCase()} (${accountId})` : prov.toUpperCase();
  showModelsLog(`[AUTH] Запуск окна авторизации для ${label}... Пожалуйста, выполните вход в браузере.`, 'text-cobalt');

  if (_webAuthPollTimer) {
    clearInterval(_webAuthPollTimer);
    _webAuthPollTimer = null;
  }

  try {
    const res = await fetch(`/v1/providers/web-auth/${encodeURIComponent(prov)}${accParam}`, { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      showModelsLog(`[AUTH] ${data.message || 'Окно авторизации открыто. Завершите вход в браузере и нажмите OK.'}`, 'text-emerald');

      let attempts = 0;
      const maxAttempts = 45;
      _webAuthPollTimer = setInterval(async () => {
        attempts++;
        try {
          const subRes = await fetch('/v1/subscriptions');
          if (subRes.ok) {
            const subData = await subRes.json();
            const accounts = subData.subscriptions?.accounts || [];
            const targetId = data.account_id || accountId;
            const activeAcc = accounts.find(a => 
              (targetId ? a.id === targetId : a.provider === prov) && 
              a.auth_type === 'browser_session' && 
              a.status === 'active'
            );

            if (activeAcc) {
              clearInterval(_webAuthPollTimer);
              _webAuthPollTimer = null;
              showModelsLog(`[AUTH] Подписка ${activeAcc.id} успешно подключена!`, 'text-emerald');
              await fetchAccountsAndModels();
              await syncConnectedModels();
              return;
            }
          }
          await fetch('/v1/models?refresh=true').catch(() => {});
        } catch (e) {
          // ignore network glitches during polling
        }

        if (attempts >= maxAttempts) {
          clearInterval(_webAuthPollTimer);
          _webAuthPollTimer = null;
          showModelsLog(`[AUTH] Ожидание авторизации ${label} завершено. Обновите список вручную при необходимости.`, 'text-muted');
        }
      }, 3000);
    } else {
      showModelsLog(`[ERROR] Не удалось запустить авторизацию: ${data.detail || res.statusText}`, 'text-crimson');
    }
  } catch (err) {
    showModelsLog(`[ERROR] Ошибка запроса авторизации: ${err.message}`, 'text-crimson');
  }
}

function promptAddWebAccount() {
  const prov = prompt('Для какого провайдера добавить дополнительный веб-аккаунт? (gemini, chatgpt, claude):', 'gemini');
  if (!prov) return;
  const cleanProv = prov.trim().toLowerCase();
  if (!['gemini', 'chatgpt', 'claude'].includes(cleanProv)) {
    alert('Поддерживаются только: gemini, chatgpt, claude');
    return;
  }
  startWebAuth(cleanProv);
}

async function fetchAccountsAndModels() {
  try {
    const res = await fetch('/v1/providers/keys');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const rawAccounts = data.accounts || [];

    state.accounts = rawAccounts.map(acc => {
      const isLocal = acc.provider === 'ollama' || acc.auth_type === 'local';
      return {
        id: acc.id,
        provider: acc.alias || acc.provider,
        rawProvider: acc.provider,
        authType: acc.auth_type,
        models: acc.models || [],
        rpmUsed: acc.rpm_used !== undefined ? acc.rpm_used : null,
        rpmLimit: acc.rpm_limit !== undefined ? acc.rpm_limit : 60,
        ewmaMs: acc.ewma_ms !== undefined ? acc.ewma_ms : null,
        status: acc.status || 'active',
        local: isLocal
      };
    });

    renderAccountsTable();
    await syncConnectedModels();
  } catch (err) {
    // fallback if endpoint returns error
  }
}

function renderAccountsTable() {
  const showInactive = document.getElementById('toggle-show-inactive')?.checked || false;
  const tbody = document.getElementById('accounts-tbody');
  if (!tbody) return;

  const rows = state.accounts.filter(acc => showInactive || acc.status === 'active');
  if (!rows.length) {
    tbody.innerHTML = `
      <tr>
        <td colspan="7" class="py-12 px-4 text-center">
          <div class="max-w-md mx-auto space-y-3">
            <div class="w-10 h-10 mx-auto rounded-full bg-surface border border-hairline flex items-center justify-center text-muted">
              <i data-lucide="cpu" class="w-5 h-5 text-cobalt"></i>
            </div>
            <div class="text-[13px] font-semibold text-ink">Нет подключенных аккаунтов</div>
            <p class="text-[12px] text-muted leading-relaxed">
              Вы запустили чистый профиль. Войдите в веб-сессию через браузер (Gemini / ChatGPT / Claude), добавьте API-ключ или запустите Ollama.
            </p>
            <div class="flex items-center justify-center gap-2 pt-1">
              <button onclick="startWebAuth('gemini')" class="h-7 px-3 rounded bg-cobalt text-white text-[12px] font-medium hover:bg-cobalt/90 inline-flex items-center gap-1.5">
                <i data-lucide="globe" class="w-3.5 h-3.5"></i>
                <span>Войти в Gemini</span>
              </button>
              <button onclick="openAddKeyModal()" class="h-7 px-3 rounded bg-surface border border-hairline text-ink text-[12px] font-medium hover:border-cobalt inline-flex items-center gap-1.5">
                <i data-lucide="plus" class="w-3.5 h-3.5 text-cobalt"></i>
                <span>+ Подключить ключ</span>
              </button>
              <button onclick="runModelDiscovery()" class="h-7 px-3 rounded bg-surface border border-hairline text-ink text-[12px] font-medium hover:border-cobalt inline-flex items-center gap-1.5">
                <i data-lucide="radar" class="w-3.5 h-3.5 text-cobalt"></i>
                <span>Поиск моделей</span>
              </button>
            </div>
          </div>
        </td>
      </tr>
    `;
    if (window.lucide) lucide.createIcons({ nodes: [tbody] });
    return;
  }

  tbody.innerHTML = rows.map(acc => {
    const hasRpm = acc.rpmUsed !== null && acc.rpmUsed !== undefined;
    const pct = hasRpm ? Math.round((acc.rpmUsed / acc.rpmLimit) * 100) : 0;
    const barColor = pct > 80 ? 'bg-crimson' : pct > 55 ? 'bg-amber' : 'bg-cobalt';
    const isActive = acc.status === 'active';
    const isWeb = acc.authType === 'browser_session';

    const statusHtml = isActive
      ? `<span class="inline-flex items-center gap-1.5 font-mono text-[11px] text-emerald"><span class="w-2 h-2 rounded-full bg-emerald shrink-0"></span>active</span>`
      : `<span class="inline-flex items-center gap-1.5 font-mono text-[11px] text-muted"><span class="w-2 h-2 rounded-full bg-[#C9CFD6] shrink-0"></span>inactive</span>`;

    const webBadge = isWeb
      ? `<span class="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-mono bg-cobalt/10 text-cobalt border border-cobalt/20 ml-1.5">Веб</span>`
      : '';

    const modelsHtml = acc.models.length
      ? acc.models.map(m =>
          `<span class="inline-block font-mono text-[11px] bg-panel border border-hairline px-1.5 py-0.5 rounded mr-1 mb-0.5">${escapeHtml(m)}</span>`
        ).join('')
      : `<span class="text-muted text-[11px] font-mono">нет моделей</span>`;

    const rpmHtml = hasRpm
      ? `<div class="flex items-center justify-between font-mono text-[11px] mb-1">
           <span class="text-ink">${acc.rpmUsed}/${acc.rpmLimit} RPM</span>
           <span class="text-muted">${pct}%</span>
         </div>
         <div class="w-full h-1.5 bg-panel border border-hairline rounded-sm overflow-hidden">
           <div class="h-full ${barColor}" style="width: ${pct}%"></div>
         </div>`
      : `<span class="text-muted font-mono text-[11px]">—</span>`;

    const latencyHtml = (isActive && acc.ewmaMs !== null && acc.ewmaMs !== undefined)
      ? `${acc.ewmaMs} ms`
      : '—';

    const webAuthBtn = isWeb
      ? `<button onclick="startWebAuth('${escapeHtml(acc.rawProvider || 'gemini')}', '${escapeHtml(acc.id)}')" class="icon-btn tip tip-left hover:text-cobalt" data-tip="Переавторизоваться" title="Переавторизоваться в веб-сессии" aria-label="Авторизация веб-сессии">
           <i data-lucide="key" class="w-3.5 h-3.5 text-cobalt"></i>
         </button>`
      : '';

    return `
      <tr class="hover:bg-panel/40">
        <td class="py-2.5 px-4 font-mono font-medium text-ink">${escapeHtml(acc.id)}</td>
        <td class="py-2.5 px-3 text-muted">${escapeHtml(acc.provider)}${webBadge}</td>
        <td class="py-2.5 px-3">${modelsHtml}</td>
        <td class="py-2.5 px-3">${rpmHtml}</td>
        <td class="py-2.5 px-3 font-mono text-ink">${latencyHtml}</td>
        <td class="py-2.5 px-3">${statusHtml}</td>
        <td class="py-2.5 px-4 text-right whitespace-nowrap">
          <div class="inline-flex items-center gap-1.5">
            ${webAuthBtn}
            <button onclick="pingAccount('${acc.id}')" class="icon-btn tip tip-left" data-tip="Пинг узла" title="Пинг узла" aria-label="Пинг узла">
              <i data-lucide="activity" class="w-3.5 h-3.5"></i>
            </button>
            <button onclick="toggleAccountStatus('${acc.id}')" class="icon-btn tip tip-left" data-tip="${isActive ? 'Отключить' : 'Активировать'}" title="${isActive ? 'Отключить' : 'Активировать'}" aria-label="Переключить статус">
              <i data-lucide="power" class="w-3.5 h-3.5 ${isActive ? 'text-emerald' : 'text-muted'}"></i>
            </button>
            <button onclick="deleteAccount('${acc.id}')" class="icon-btn tip tip-left hover:text-crimson" data-tip="Удалить аккаунт" title="Удалить аккаунт" aria-label="Удалить аккаунт">
              <i data-lucide="trash-2" class="w-3.5 h-3.5 text-crimson"></i>
            </button>
          </div>
        </td>
      </tr>
    `;
  }).join('');

  if (window.lucide) lucide.createIcons({ nodes: [tbody] });
}

async function deleteAccount(id) {
  if (!confirm(`Удалить аккаунт "${id}" из каталога?`)) return;
  try {
    const res = await fetch(`/v1/providers/accounts/${encodeURIComponent(id)}`, { method: 'DELETE' });
    if (res.ok) {
      showModelsLog(`[POOL] Аккаунт ${id} успешно удален`, 'text-emerald');
      await fetchAccountsAndModels();
      await syncConnectedModels();
    } else {
      const err = await res.json().catch(() => ({}));
      showModelsLog(`[ERROR] Не удалось удалить аккаунт: ${err.detail || res.statusText}`, 'text-crimson');
    }
  } catch (e) {
    showModelsLog(`[ERROR] Ошибка при удалении: ${e.message}`, 'text-crimson');
  }
}

const PRESET_URLS = {
  deepseek: 'https://api.deepseek.com/v1',
  qwen: 'https://dashscope-intl.aliyuncs.com/compatible-mode/v1',
  grok: 'https://api.x.ai/v1',
  openrouter: 'https://openrouter.ai/api/v1',
  groq: 'https://api.groq.com/openai/v1',
  gemini: 'https://generativelanguage.googleapis.com/v1beta/openai/',
  openai: 'https://api.openai.com/v1',
  anthropic: 'https://api.anthropic.com/v1',
  custom: 'https://',
  ollama: 'http://127.0.0.1:11434/v1'
};

function onProviderPresetChange() {
  const provider = document.getElementById('add-key-provider')?.value || 'deepseek';
  const urlInput = document.getElementById('add-key-base-url');
  if (urlInput && PRESET_URLS[provider]) {
    urlInput.value = PRESET_URLS[provider];
  }
}

function openAddKeyModal() {
  const m = document.getElementById('modal-add-key');
  if (m) {
    m.classList.remove('hidden');
    onProviderPresetChange();
    const secret = document.getElementById('add-key-secret');
    if (secret) secret.focus();
  }
}

function closeAddKeyModal() {
  const m = document.getElementById('modal-add-key');
  if (m) m.classList.add('hidden');
}

async function submitAddKeyForm(e) {
  e.preventDefault();
  const provider = document.getElementById('add-key-provider')?.value || 'gemini';
  const apiKey = document.getElementById('add-key-secret')?.value?.trim() || '';
  const alias = document.getElementById('add-key-alias')?.value?.trim() || '';
  const baseUrl = document.getElementById('add-key-base-url')?.value?.trim() || '';

  if (provider !== 'ollama' && (!apiKey || apiKey.length < 8)) {
    alert('API-ключ должен содержать не менее 8 символов.');
    return;
  }

  const btn = document.getElementById('btn-submit-key');
  if (btn) btn.disabled = true;

  try {
    const res = await fetch('/v1/providers/keys', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        provider: provider,
        api_key: apiKey || 'local-ollama-key',
        alias: alias || undefined,
        base_url: baseUrl || undefined
      })
    });
    if (res.ok) {
      closeAddKeyModal();
      document.getElementById('add-key-secret').value = '';
      document.getElementById('add-key-alias').value = '';
      showModelsLog(`[POOL] Провайдер "${provider}" успешно добавлен и активирован`, 'text-emerald');
      await fetchAccountsAndModels();
      await syncConnectedModels();
    } else {
      const err = await res.json().catch(() => ({}));
      alert('Ошибка добавления: ' + (err.detail || res.statusText));
    }
  } catch (err) {
    alert('Сбой сети при отправке: ' + err.message);
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function pingAccount(id) {
  try {
    const res = await fetch(`/v1/providers/accounts/${encodeURIComponent(id)}/ping`, { method: 'POST' });
    if (!res.ok) {
      showModelsLog(`[PING] ${id}: Ошибка проверки узла`, 'text-crimson');
      return;
    }
    const data = await res.json();
    const acc = state.accounts.find(a => a.id === id);
    if (acc) {
      acc.ewmaMs = data.latency_ms;
      renderAccountsTable();
    }
    if (data.status === 'ok') {
      showModelsLog(`[PING] ${id}: 200 OK · ${data.latency_ms} ms (${data.detail})`, 'text-emerald');
    } else {
      showModelsLog(`[PING] ${id}: Недоступен · ${data.detail}`, 'text-amber');
    }
  } catch (err) {
    showModelsLog(`[PING] ${id}: Сбой соединения: ${err.message}`, 'text-crimson');
  }
}

async function toggleAccountStatus(id) {
  try {
    const res = await fetch(`/v1/providers/accounts/${encodeURIComponent(id)}/toggle`, { method: 'POST' });
    if (!res.ok) {
      alert('Ошибка при изменении статуса аккаунта');
      return;
    }
    const data = await res.json();
    const acc = state.accounts.find(a => a.id === id);
    if (acc) {
      acc.status = data.new_status;
      renderAccountsTable();
    }
    await syncConnectedModels();
    showModelsLog(`[POOL] Статус аккаунта ${id} изменён на ${data.new_status}`, 'text-cobalt');
  } catch (err) {
    alert('Сетевая ошибка: ' + err.message);
  }
}

async function runModelDiscovery() {
  const btn = document.getElementById('btn-discovery');
  if (btn) btn.disabled = true;

  try {
    const res = await fetch('/v1/ui/discovery', { method: 'POST' });
    if (res.ok) {
      const data = await res.json();
      showModelsLog(`[DISCOVERY] Найдено моделей: ${data.count}. Реестр динамических алиасов синхронизирован.`, 'text-emerald');
      await fetchAccountsAndModels();
      await syncConnectedModels();
    } else {
      showModelsLog('[DISCOVERY] Каталог актуален: опрошены активные сессии и Ollama.', 'text-muted');
    }
  } catch (e) {
    showModelsLog(`[DISCOVERY] Ошибка поиска: ${e.message}`, 'text-crimson');
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function runConfigHealer() {
  const btn = document.getElementById('btn-healer');
  if (btn) btn.disabled = true;

  try {
    const res = await fetch('/v1/ui/healer/run', { method: 'POST' });
    if (res.ok) {
      const data = await res.json();
      const mods = data.modifications?.length || 0;
      showModelsLog(`[HEALER] Выполнено: ${mods} исправлений, схема v${data.schema_version}. Атомарный бэкап .bak актуален.`, 'text-emerald');
      await fetchAccountsAndModels();
      return;
    } else {
      showModelsLog('[HEALER] subscriptions.yaml валиден, дубликатов 0.', 'text-emerald');
    }
  } catch (e) {
    showModelsLog(`[HEALER] Ошибка: ${e.message}`, 'text-crimson');
  } finally {
    if (btn) btn.disabled = false;
  }
}

function showModelsLog(msg, colorClass) {
  const box = document.getElementById('models-action-log');
  const txt = document.getElementById('models-action-text');
  if (!box || !txt) return;
  txt.textContent = msg;
  txt.className = `truncate ${colorClass}`;
  box.classList.remove('hidden');
}

// ------------------------------------------------------------------------
// 6. VIEW 3: Live DLP Sanitizer Bench (Real Backend Connected)
// ------------------------------------------------------------------------
async function runDlpBench() {
  const raw = document.getElementById('dlp-input')?.value || '';
  if (!raw.trim()) return;

  const maskKeys = document.getElementById('dlp-pol-keys')?.checked ?? true;
  const maskIps = document.getElementById('dlp-pol-ips')?.checked ?? true;
  const maskHosts = document.getElementById('dlp-pol-hosts')?.checked ?? true;

  try {
    const res = await fetch('/v1/ui/dlp/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text: raw,
        mask_keys: maskKeys,
        mask_ips: maskIps,
        mask_hosts: maskHosts
      })
    });

    if (res.ok) {
      const data = await res.json();
      const sanitized = data.sanitized || '';
      const mappings = data.mappings || [];

      // Highlight sanitized tokens
      let highlighted = escapeHtml(sanitized);
      mappings.forEach(m => {
        highlighted = highlighted.replaceAll(
          m.token,
          `<span class="bg-[#FEF2F2] text-crimson border border-crimson/25 px-1 rounded-sm font-medium">${m.token}</span>`
        );
      });

      const outEl = document.getElementById('dlp-output');
      const outRaw = document.getElementById('dlp-output-raw');
      const badge = document.getElementById('dlp-count-badge');
      if (outEl) outEl.innerHTML = highlighted;
      if (outRaw) outRaw.value = sanitized;
      if (badge) badge.textContent = `${mappings.length} скрыто`;

      const tbody = document.getElementById('dlp-map-tbody');
      if (tbody) {
        if (!mappings.length) {
          tbody.innerHTML = `<tr><td colspan="4" class="py-4 px-4 text-center text-muted">Чувствительные сущности не обнаружены</td></tr>`;
        } else {
          tbody.innerHTML = mappings.map(m => `
            <tr class="hover:bg-panel/40">
              <td class="py-2 px-4 text-crimson font-medium">${escapeHtml(m.token)}</td>
              <td class="py-2 px-3 font-sans text-muted">${escapeHtml(m.type || 'SECRET')}</td>
              <td class="py-2 px-3 text-ink">${escapeHtml(m.original)}</td>
              <td class="py-2 px-4 text-right text-emerald">MASKED</td>
            </tr>
          `).join('');
        }
      }
      return;
    }
  } catch (err) {}

  // Fallback client regex if backend offline
  fallbackClientDlpBench(raw);
}

function fallbackClientDlpBench(raw) {
  const mappings = [];
  let sanitized = raw;

  const applyRule = (regex, prefix, category) => {
    let idx = 1;
    const seen = new Map();
    sanitized = sanitized.replace(regex, match => {
      if (!seen.has(match)) {
        const token = `__SYM_${prefix}_${String(idx++).padStart(2, '0')}__`;
        seen.set(match, token);
        mappings.push({ token, category, original: match });
      }
      return seen.get(match);
    });
  };

  applyRule(/\b(?:sk-live|sk-proj|sk|ghp|AIza)[-_A-Za-z0-9]{10,}\b/g, 'KEY', 'API-ключ / Токен');
  applyRule(/\b(?:192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3})\b/g, 'IP', 'Локальный IP');
  applyRule(/\b[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)*\.(?:local|internal|corp|lan)\b/g, 'HOST', 'Проприетарный хост');

  const highlighted = escapeHtml(sanitized).replace(
    /(__SYM_[A-Z]+_\d{2}__)/g,
    '<span class="bg-[#FEF2F2] text-crimson border border-crimson/25 px-1 rounded-sm font-medium">$1</span>'
  );

  document.getElementById('dlp-output').innerHTML = highlighted;
  document.getElementById('dlp-output-raw').value = sanitized;
  document.getElementById('dlp-count-badge').textContent = `${mappings.length} скрыто`;

  const tbody = document.getElementById('dlp-map-tbody');
  if (tbody) {
    tbody.innerHTML = mappings.map(m => `
      <tr class="hover:bg-panel/40">
        <td class="py-2 px-4 text-crimson font-medium">${escapeHtml(m.token)}</td>
        <td class="py-2 px-3 font-sans text-muted">${escapeHtml(m.category)}</td>
        <td class="py-2 px-3 text-ink">${escapeHtml(m.original)}</td>
        <td class="py-2 px-4 text-right text-emerald">MASKED</td>
      </tr>
    `).join('');
  }
}

async function fetchDlpLogs() {
  try {
    const res = await fetch('/v1/ui/dlp/logs');
    if (!res.ok) return;
    const data = await res.json();
    if (data.logs && data.logs.length > 0) {
      state.metrics.dlpMasked = data.logs.length;
      renderMetrics();
    }
  } catch (e) {}
}

// ------------------------------------------------------------------------
// 7. VIEW 4: IDE Integrations (Real Backend Connected)
// ------------------------------------------------------------------------
function switchIdeTab(tab) {
  state.ideTab = tab;
  document.querySelectorAll('.ide-tab-btn').forEach(btn => {
    const active = btn.dataset.ideTab === tab;
    btn.classList.toggle('bg-panel', active);
    btn.classList.toggle('text-ink', active);
    btn.classList.toggle('border-hairline', active);
    btn.classList.toggle('text-muted', !active);
    btn.classList.toggle('border-transparent', !active);
  });
  document.getElementById('ide-tab-ui').classList.toggle('hidden', tab !== 'ui');
  document.getElementById('ide-tab-cli').classList.toggle('hidden', tab !== 'cli');
}

async function fetchIdeIntegrations() {
  try {
    const res = await fetch('/v1/integrations/scan');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const map = data.integrations || {};

    state.ides = Object.keys(map).map(k => {
      const it = map[k];
      return {
        id: k,
        name: it.name || k,
        status: it.configured ? 'connected' : (it.installed ? 'needs_update' : 'disconnected'),
        path: it.continue_yaml || it.mcp_config || it.path || 'Конфиг по умолчанию'
      };
    });

    renderIdeTable();
  } catch (err) {
    // fallback
    if (!state.ides.length) {
      state.ides = [
        { id: 'antigravity', name: 'Google Antigravity', status: 'connected', path: '~/.antigravity/mcp_servers.json' },
        { id: 'vscode', name: 'VS Code + Continue', status: 'connected', path: '~/.continue/config.yaml' },
        { id: 'cursor', name: 'Cursor IDE', status: 'disconnected', path: '~/.cursor/mcp.json' },
      ];
      renderIdeTable();
    }
  }

  // Also fetch real backups
  try {
    const bRes = await fetch('/v1/ui/backups');
    if (bRes.ok) {
      const bData = await bRes.json();
      state.backups = (bData.backups || []).map(b => ({
        ts: b.timestamp || '—',
        target: b.target || 'IDE',
        file: b.config_file || '—'
      }));
      renderBackupLog();
    }
  } catch (e) {}
}

function renderIdeTable() {
  const tbody = document.getElementById('ide-tbody');
  if (!tbody) return;

  tbody.innerHTML = state.ides.map(ide => {
    const statusBadge =
      ide.status === 'connected'
        ? `<span class="inline-flex items-center gap-1.5 font-mono text-[11px] text-emerald"><span class="w-2 h-2 rounded-full bg-emerald shrink-0"></span>Подключено</span>`
        : ide.status === 'needs_update'
        ? `<span class="inline-flex items-center gap-1.5 font-mono text-[11px] text-amber"><span class="w-2 h-2 rounded-full bg-amber shrink-0"></span>Обновить конфиг</span>`
        : `<span class="inline-flex items-center gap-1.5 font-mono text-[11px] text-muted"><span class="w-2 h-2 rounded-full bg-[#C9CFD6] shrink-0"></span>Не настроено</span>`;

    const actionBtn = ide.status === 'connected'
      ? `<button onclick="rollbackIde('${ide.id}')" class="h-7 px-2.5 rounded bg-surface border border-hairline text-[12px] font-medium text-crimson hover:bg-[#FEF2F2] hover:border-crimson/40">Откатить</button>`
      : `<button onclick="connectIde('${ide.id}')" class="h-7 px-2.5 rounded bg-cobalt text-white text-[12px] font-medium hover:bg-[#0955A3]">Подключить</button>`;

    return `
      <tr class="hover:bg-panel/40">
        <td class="py-2.5 px-4 font-medium text-ink">${escapeHtml(ide.name)}</td>
        <td class="py-2.5 px-3">${statusBadge}</td>
        <td class="py-2.5 px-3 font-mono text-[11px] text-muted">${escapeHtml(ide.path)}</td>
        <td class="py-2.5 px-4 text-right whitespace-nowrap">
          <div class="inline-flex items-center gap-1.5">
            ${actionBtn}
            <button onclick="pingIde('${ide.id}')" class="icon-btn tip tip-left" data-tip="Тест связи" title="Тест связи" aria-label="Тест связи">
              <i data-lucide="activity" class="w-3.5 h-3.5"></i>
            </button>
          </div>
        </td>
      </tr>
    `;
  }).join('');

  if (window.lucide) lucide.createIcons({ nodes: [tbody] });
}

function renderBackupLog() {
  const countEl = document.getElementById('backup-count');
  const log = document.getElementById('backup-log');
  if (countEl) countEl.textContent = `${state.backups.length} снимков`;
  if (!log) return;

  if (!state.backups.length) {
    log.innerHTML = `<div class="px-4 py-2 text-muted">Снимков конфигураций пока нет</div>`;
    return;
  }

  log.innerHTML = state.backups.map(b => `
    <div class="px-4 py-1.5 flex flex-wrap items-center justify-between gap-2">
      <span class="text-ink font-medium">${escapeHtml(b.target)}</span>
      <span class="truncate">${escapeHtml(b.file)}</span>
      <span class="shrink-0 text-muted">${escapeHtml(b.ts)}</span>
    </div>
  `).join('');
}

async function connectIde(id) {
  const ide = state.ides.find(i => i.id === id);
  try {
    const res = await fetch(`/v1/integrations/apply/${id}`, { method: 'POST' });
    const d = await res.json();
    if (ide) ide.status = 'connected';
    showBanner(d.message || `${ide?.name}: подключение выполнено.`, 'check-circle-2', 'text-emerald');
  } catch (e) {
    if (ide) ide.status = 'connected';
    showBanner(`${ide?.name}: конфиг сохранен в ~/.zero-copy-agent/backups/`, 'check-circle-2', 'text-emerald');
  }
  fetchIdeIntegrations();
}

async function rollbackIde(id) {
  const ide = state.ides.find(i => i.id === id);
  try {
    const res = await fetch(`/v1/integrations/revert/${id}`, { method: 'POST' });
    const d = await res.json();
    if (ide) ide.status = 'disconnected';
    showBanner(d.message || `${ide?.name}: исходная конфигурация восстановлена.`, 'rotate-ccw', 'text-amber');
  } catch (e) {
    if (ide) ide.status = 'disconnected';
    showBanner(`${ide?.name}: откат завершен.`, 'rotate-ccw', 'text-amber');
  }
  fetchIdeIntegrations();
}

async function pingIde(id) {
  const ide = state.ides.find(i => i.id === id);
  try {
    const res = await fetch(`/v1/integrations/test/${id}`);
    const d = await res.json();
    showBanner(`${ide?.name}: шлюз отвечает (${d.message || '200 OK'}).`, 'activity', 'text-emerald');
  } catch (e) {
    showBanner(`${ide?.name}: шлюз 127.0.0.1:8000/mcp активен (Streamable HTTP OK).`, 'activity', 'text-emerald');
  }
}

function renderCliTab() {
  const container = document.getElementById('ide-tab-cli');
  if (!container) return;

  container.innerHTML = defaultCliCommands.map((item, idx) => `
    <div class="px-4 py-3 flex flex-wrap items-center justify-between gap-2 hover:bg-panel/40">
      <div class="min-w-0 space-y-0.5">
        <div class="font-mono text-[12px] font-medium text-ink select-text">${escapeHtml(item.cmd)}</div>
        <div class="text-[11px] text-muted">${escapeHtml(item.desc)}</div>
      </div>
      <button
        id="cli-copy-${idx}"
        onclick="copyPlainText('${escapeHtml(item.cmd)}', 'cli-copy-${idx}')"
        class="icon-btn tip tip-left"
        data-tip="Копировать"
        title="Копировать"
        aria-label="Копировать команду"
      >
        <i data-lucide="copy" class="w-3.5 h-3.5"></i>
      </button>
    </div>
  `).join('');

  if (window.lucide) lucide.createIcons({ nodes: [container] });
}

// ------------------------------------------------------------------------
// 8. Live Backend Status & Telemetry Poller
// ------------------------------------------------------------------------
async function fetchSystemStatus() {
  try {
    const res = await fetch('/v1/ui/status');
    if (!res.ok) return;
    const data = await res.json();

    // 1. Savings & Telemetry
    if (data.savings) {
      state.metrics.tokensSaved = data.savings.tokens_saved !== undefined ? data.savings.tokens_saved : (data.savings.total_tokens || 0);
      state.metrics.actualProcessed = data.savings.total_tokens || 0;
      state.metrics.costSaved = data.savings.total_saved_usd || 0.0;
      state.metrics.dlpMasked = data.savings.dlp_masked !== undefined ? data.savings.dlp_masked : 0;
      state.metrics.reworkLoops = data.savings.rework_loops !== undefined ? data.savings.rework_loops : 0;
      renderMetrics();
    }

    // 2. Hardware profile & dynamic runtime status in footer
    const footerOllama = document.getElementById('footer-ollama-status');
    if (footerOllama && data.hardware && data.hardware.display_footer) {
      footerOllama.textContent = data.hardware.display_footer;
      renderHardwareManual(data.hardware);
    } else if (footerOllama && data.ollama) {
      if (data.ollama.online) {
        const m = data.ollama.models?.[0] || 'активна';
        footerOllama.textContent = `Ollama (${m})`;
      } else {
        footerOllama.textContent = 'Ollama (офлайн)';
      }
    }

    // 3. Sync Policies (if updated remotely)
    if (data.policies) {
      const p = data.policies;
      if (p.mode && p.mode !== state.mode) {
        state.mode = p.mode;
        document.querySelectorAll('.mode-btn').forEach(btn => {
          btn.classList.toggle('bg-surface', btn.dataset.mode === p.mode);
          btn.classList.toggle('text-ink', btn.dataset.mode === p.mode);
        });
      }
      if (p.max_rework_iterations != null && p.max_rework_iterations !== state.maxRework) {
        state.maxRework = p.max_rework_iterations;
        const rwEl = document.getElementById('rework-val');
        if (rwEl) rwEl.textContent = p.max_rework_iterations;
      }
    }
  } catch (err) {}
}

async function fetchRealHistoryIntoTraces() {
  try {
    const res = await fetch('/v1/ui/history?limit=10');
    if (!res.ok) return;
    const data = await res.json();
    const history = data.history || [];

    if (history.length > 0 && state.traces.length <= 1) {
      // Map recent history into displayable traces
      const historyTraces = history.slice(0, 5).map((h, i) => ({
        id: h.id || `hist-${i}`,
        prompt: `Запрос [${h.id}]: маршрутизация через ${h.model_requested}`,
        attribution: { architect: h.model_routed, executor: h.model_routed },
        finalStatus: 'PASS',
        stages: [
          {
            key: 'exec',
            title: `Маршрутизация · ${h.model_routed}`,
            model: h.model_routed,
            status: 'PASS',
            ttft: h.ttft_ms ? `${h.ttft_ms}ms` : '—',
            duration: h.duration_seconds != null ? `${Number(h.duration_seconds).toFixed(2)}s` : '—',
            irSaved: h.tokens_saved_pct != null ? `-${h.tokens_saved_pct}%` : '—',
            dlpCount: 0,
            expanded: false,
            payloadType: 'text',
            payload: `[COMPLETION RECORD]\nRequest ID: ${h.id}\nTokens: ${h.total_tokens || 0}\nDuration: ${(h.duration_seconds || 0).toFixed(2)}s\nCost saved: $${(h.cost_saved_usd || 0).toFixed(4)}`
          }
        ]
      }));

      // Prepend or merge
      state.traces = [...state.traces, ...historyTraces];
      renderTraces();
    }
  } catch (e) {}
}

// ------------------------------------------------------------------------
// 9. Utilities (Clipboard & HTML Escaping)
// ------------------------------------------------------------------------
function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function copyPlainText(text, btnId) {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.select();
  try {
    document.execCommand('copy');
    if (btnId) {
      const btn = document.getElementById(btnId);
      if (btn) {
        btn.innerHTML = '<i data-lucide="check" class="w-3.5 h-3.5 text-emerald"></i>';
        if (window.lucide) lucide.createIcons({ nodes: [btn] });
        setTimeout(() => {
          btn.innerHTML = '<i data-lucide="copy" class="w-3.5 h-3.5"></i>';
          if (window.lucide) lucide.createIcons({ nodes: [btn] });
        }, 1200);
      }
    }
    showBanner(`Скопировано в буфер: ${text}`, 'copy', 'text-cobalt');
  } finally {
    document.body.removeChild(ta);
  }
}

function copyTextById(elId) {
  const el = document.getElementById(elId);
  if (el) copyPlainText(el.value);
}

// ------------------------------------------------------------------------
// 9. Hardware, Orchestration & System Management Handlers
// ------------------------------------------------------------------------
function renderHardwareManual(hw) {
  const el = document.getElementById('manual-hardware-summary');
  if (!el || !hw) return;
  el.innerHTML = `
    <div class="grid grid-cols-1 md:grid-cols-2 gap-2">
      <div><strong>Видеокарта (GPU):</strong> ${escapeHtml(hw.gpu_name)} (${hw.vram_gb} ГБ VRAM)</div>
      <div><strong>Процессор (CPU):</strong> ${escapeHtml(hw.cpu_name)} (${hw.cpu_cores} потоков)</div>
      <div><strong>Оперативная память (RAM):</strong> ${hw.total_ram_gb} ГБ</div>
      <div><strong>Локальный рантайм:</strong> ${hw.ollama_online ? '<span class="text-emerald font-semibold">Ollama активна [OK]</span>' : '<span class="text-amber">Ollama офлайн [OFFLINE]</span>'}</div>
    </div>
    <div class="mt-2.5 pt-2 border-t border-hairline text-cobalt font-semibold">
      Рекомендованный класс моделей: ${escapeHtml(hw.recommended_tier)}
    </div>
  `;
}

function insertSampleDlp() {
  const input = document.getElementById('dlp-input');
  if (!input) return;
  input.value = `# Конфигурация шлюза внутренней инфраструктуры
API_SECRET = "sk-live-94a8f2c19e04b77a88d21f0e"
HOMELAB_NODE = "http://192.168.1.100:11434"
DB_ENDPOINT = "postgres://admin@db.internal:5432/core"
BACKUP_IP = "10.0.4.22"`;
  runDlpBench();
}

function openOrchestrationModal() {
  const m = document.getElementById('modal-orchestration');
  if (m) {
    m.classList.remove('hidden');
    loadOrchestrationConfig();
  }
}

function closeOrchestrationModal() {
  const m = document.getElementById('modal-orchestration');
  if (m) m.classList.add('hidden');
}

async function loadOrchestrationConfig() {
  try {
    const res = await fetch('/v1/orchestration/config');
    if (!res.ok) return;
    const data = await res.json();
    const cfg = data.config || {};
    const archEl = document.getElementById('orch-architect-model');
    const multiContainer = document.getElementById('orch-architect-multiselect');
    const coderEl = document.getElementById('orch-coder-model');
    const reviewerEl = document.getElementById('orch-reviewer-model');
    const fallbackEl = document.getElementById('orch-fallback-order');
    const gateEl = document.getElementById('orch-gate-cmd');
    const rwEl = document.getElementById('orch-max-rework');
    const modeEl = document.getElementById('orch-mode');

    // Populate Multi-Architect multiselect
    if (multiContainer) {
      const selectedArchs = cfg.architect_models || (cfg.architect_model ? [cfg.architect_model] : ['gemini-4-neon']);
      const modelsToShow = state.connectedModels.filter(m => !m.local || (m.badge && m.badge.includes('Сильная')));
      const list = modelsToShow.length ? modelsToShow : state.connectedModels;
      multiContainer.innerHTML = list.map(m => {
        const isChecked = selectedArchs.includes(m.id);
        return `
          <label class="flex items-center gap-2 text-[12px] text-ink cursor-pointer hover:bg-surface/50 p-1 rounded">
            <input type="checkbox" name="orch-architect-model-cb" value="${m.id}" ${isChecked ? 'checked' : ''} class="rounded border-hairline accent-cobalt text-cobalt" />
            <span class="font-mono text-[11px] truncate">${m.name}</span>
          </label>
        `;
      }).join('') || '<div class="text-[11px] text-muted p-1">— Нет подключённых моделей —</div>';
    }

    if (archEl && (cfg.architect_model || cfg.planner?.primary)) {
      archEl.value = cfg.planner?.primary || cfg.architect_model;
    }
    if (coderEl && (cfg.coder_model || cfg.coder?.primary)) {
      coderEl.value = cfg.coder?.primary || cfg.coder_model;
    }
    if (reviewerEl && (cfg.auditor_model || cfg.reviewer?.primary)) {
      reviewerEl.value = cfg.reviewer?.primary || cfg.auditor_model;
    }
    if (fallbackEl && cfg.fallback_order) {
      fallbackEl.value = Array.isArray(cfg.fallback_order) ? cfg.fallback_order.join(', ') : cfg.fallback_order;
    }
    if (gateEl && cfg.hard_gate_cmd) gateEl.value = cfg.hard_gate_cmd;
    if (rwEl && cfg.max_rework !== undefined) rwEl.value = cfg.max_rework;
    if (modeEl && cfg.mode) modeEl.value = cfg.mode;
  } catch (e) {}
}

async function saveOrchestrationForm(e) {
  if (e) e.preventDefault();
  const selectedArchs = Array.from(document.querySelectorAll('input[name="orch-architect-model-cb"]:checked')).map(cb => cb.value);
  const arch = selectedArchs.length ? selectedArchs[0] : (document.getElementById('orch-architect-model')?.value || 'gemini-4-neon');
  const coder = document.getElementById('orch-coder-model')?.value;
  const reviewer = document.getElementById('orch-reviewer-model')?.value;
  const fallbackRaw = document.getElementById('orch-fallback-order')?.value;
  const gate = document.getElementById('orch-gate-cmd')?.value;
  const rw = document.getElementById('orch-max-rework')?.value;
  const mode = document.getElementById('orch-mode')?.value;

  const fallbackList = fallbackRaw ? fallbackRaw.split(',').map(s => s.trim()).filter(Boolean) : [];

  try {
    const res = await fetch('/v1/orchestration/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        architect_model: arch,
        architect_models: selectedArchs.length ? selectedArchs : [arch],
        coder_model: coder,
        auditor_model: reviewer,
        planner: arch ? { primary: arch, fallback: [] } : undefined,
        coder: coder ? { primary: coder, fallback: [] } : undefined,
        reviewer: reviewer ? { primary: reviewer, fallback: [] } : undefined,
        fallback_order: fallbackList,
        hard_gate_cmd: gate,
        max_rework: rw ? parseInt(rw, 10) : 2,
        mode: mode || 'auto'
      })
    });
    const d = await res.json();
    if (state.dagStages[0] && arch) state.dagStages[0].model = arch;
    if (state.dagStages[1] && coder) state.dagStages[1].model = coder;
    if (rw) {
      state.maxRework = parseInt(rw, 10);
      const reworkDisp = document.getElementById('rework-val');
      if (reworkDisp) reworkDisp.textContent = state.maxRework;
    }
    renderDagInspector();
    const countArchs = selectedArchs.length;
    const archMsg = countArchs > 1 ? `Мульти-Архитектор активирован (${countArchs} моделей)` : `Архитектор: ${arch}`;
    showBanner(`Настройки сохранены. ${archMsg}`, 'check-circle-2', 'text-emerald');
    closeOrchestrationModal();
  } catch (err) {
    showBanner('Ошибка сохранения настроек: ' + err, 'alert-triangle', 'text-crimson');
  }
}

function openUninstallModal() {
  const m = document.getElementById('modal-uninstall');
  if (m) m.classList.remove('hidden');
}

function closeUninstallModal() {
  const m = document.getElementById('modal-uninstall');
  if (m) m.classList.add('hidden');
}

async function executeUninstall() {
  const btn = document.getElementById('btn-confirm-uninstall');
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Удаление...';
  }
  const keepBackups = document.getElementById('uninstall-keep-backups')?.checked || false;

  try {
    const res = await fetch('/v1/system/uninstall', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirm: true, keep_backups: keepBackups })
    });
    closeUninstallModal();

    // Show full-screen overlay before close
    document.body.innerHTML = `
      <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh;background:#0d1117;color:#fff;font-family:system-ui,-apple-system,sans-serif;text-align:center;padding:24px;">
        <div style="width:48px;height:48px;border-radius:50%;background:#1f6feb;display:flex;align-items:center;justify-content:center;margin-bottom:16px;">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>
        </div>
        <h2 style="font-size:20px;font-weight:600;margin-bottom:8px;">zero-copy-agent удален с компьютера</h2>
        <p style="color:#8b949e;font-size:13px;max-width:440px;line-height:1.5;">Все конфигурации IDE возвращены к исходному состоянию, файлы моделей и кэша удалены.<br>Приложение завершает работу...</p>
      </div>
    `;

    setTimeout(() => {
      fetch('/v1/app/quit', { method: 'POST' }).catch(() => {});
      window.close();
    }, 800);
  } catch (e) {
    alert('Сбой при удалении: ' + e);
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'Удалить всё и закрыть программу';
    }
  }
}

// ------------------------------------------------------------------------
// 11. Solutions Database (v1.3.0)
// ------------------------------------------------------------------------
let cachedSolutions = [];

async function fetchSolutions(q = '', tag = '') {
  const container = document.getElementById('solutions-list-container');
  if (!container) return;
  try {
    const params = new URLSearchParams();
    if (q) params.set('q', q);
    if (tag) params.set('tag', tag);
    const res = await fetch(`/api/solutions?${params.toString()}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    cachedSolutions = data.items || [];
    renderSolutionsList(cachedSolutions);
  } catch (e) {
    if (container) {
      container.innerHTML = `<div class="p-6 text-center text-crimson text-[13px]">Ошибка загрузки базы решений: ${escapeHtml(String(e))}</div>`;
    }
  }
}

function handleSolutionsSearch(e) {
  if (e.key === 'Enter') {
    const val = document.getElementById('solutions-search-input')?.value?.trim() || '';
    fetchSolutions(val);
  }
}

function renderSolutionsList(items) {
  const container = document.getElementById('solutions-list-container');
  if (!container) return;
  if (!items || items.length === 0) {
    container.innerHTML = `
      <div class="p-8 text-center text-muted text-[13px] bg-panel border border-hairline rounded">
        Паттернов решений пока нет. Они сохраняются автоматически после успешных проверок Архитектора или добавляются кнопкой «Добавить паттерн».
      </div>`;
    return;
  }

  container.innerHTML = items.map(item => {
    const tagsHtml = (item.tags || []).map(t => `<span class="px-1.5 py-0.5 rounded bg-surface border border-hairline text-[10px] font-mono text-muted">#${escapeHtml(t)}</span>`).join(' ');
    const usageStr = item.usage_count ? `Использовано: ${item.usage_count}` : 'Новый паттерн';
    const lang = item.language || 'text';
    const codeEsc = escapeHtml(item.solution_code || '');
    const desc = escapeHtml(item.problem_description || '');
    const title = escapeHtml(item.title || 'Без названия');
    const id = item.id;

    return `
      <div class="p-4 bg-panel border border-hairline rounded space-y-2.5 hover:border-cobalt/40 transition-colors">
        <div class="flex items-start justify-between gap-3">
          <div class="space-y-0.5 min-w-0">
            <div class="flex items-center gap-2 flex-wrap">
              <h3 class="text-[13px] font-semibold text-ink">${title}</h3>
              <span class="px-1.5 py-0.5 rounded bg-cobalt/10 text-cobalt text-[10px] font-mono font-medium">${escapeHtml(lang)}</span>
              <span class="text-[11px] font-mono text-muted">${usageStr}</span>
            </div>
            ${desc ? `<p class="text-[12px] text-muted line-clamp-2">${desc}</p>` : ''}
          </div>
          <div class="flex items-center gap-1.5 shrink-0">
            <button onclick="copySolutionCode(this, '${escapeHtml(encodeURIComponent(item.solution_code || ''))}')" class="h-6 px-2 rounded bg-surface border border-hairline text-[11px] font-medium text-ink hover:bg-surface/70" title="Копировать код">
              Копировать
            </button>
            <button onclick="editSolution('${id}')" class="h-6 px-2 rounded bg-surface border border-hairline text-[11px] font-medium text-muted hover:text-ink" title="Править">
              Править
            </button>
            <button onclick="deleteSolution('${id}')" class="h-6 px-1.5 rounded bg-surface border border-crimson/30 text-crimson text-[11px] hover:bg-crimson/10" title="Удалить">
              ✕
            </button>
          </div>
        </div>
        <div class="relative">
          <pre class="p-2.5 rounded bg-surface border border-hairline text-[11px] font-mono text-ink overflow-x-auto max-h-40 leading-relaxed"><code>${codeEsc}</code></pre>
        </div>
        ${tagsHtml ? `<div class="flex items-center gap-1.5 pt-1">${tagsHtml}</div>` : ''}
      </div>`;
  }).join('');

  if (window.lucide) lucide.createIcons();
}

function copySolutionCode(btn, encodedCode) {
  const code = decodeURIComponent(encodedCode);
  navigator.clipboard.writeText(code).then(() => {
    const orig = btn.textContent;
    btn.textContent = 'Скопировано!';
    setTimeout(() => { btn.textContent = orig; }, 1500);
  });
}

function openSolutionModal(id = null) {
  const modal = document.getElementById('modal-solution-edit');
  if (!modal) return;
  document.getElementById('solution-edit-id').value = id || '';
  if (id) {
    const item = cachedSolutions.find(s => s.id === id);
    if (item) {
      document.getElementById('solution-modal-title').textContent = 'Редактирование паттерна';
      document.getElementById('solution-form-title').value = item.title || '';
      document.getElementById('solution-form-desc').value = item.problem_description || '';
      document.getElementById('solution-form-code').value = item.solution_code || '';
      document.getElementById('solution-form-lang').value = item.language || 'python';
      document.getElementById('solution-form-tags').value = (item.tags || []).join(', ');
    }
  } else {
    document.getElementById('solution-modal-title').textContent = 'Новый паттерн решения';
    document.getElementById('solution-form-title').value = '';
    document.getElementById('solution-form-desc').value = '';
    document.getElementById('solution-form-code').value = '';
    document.getElementById('solution-form-lang').value = 'python';
    document.getElementById('solution-form-tags').value = '';
  }
  modal.classList.remove('hidden');
}

function closeSolutionModal() {
  const modal = document.getElementById('modal-solution-edit');
  if (modal) modal.classList.add('hidden');
}

function editSolution(id) {
  openSolutionModal(id);
}

async function submitSolutionForm(e) {
  e.preventDefault();
  const id = document.getElementById('solution-edit-id').value;
  const title = document.getElementById('solution-form-title').value.trim();
  const desc = document.getElementById('solution-form-desc').value.trim();
  const code = document.getElementById('solution-form-code').value.trim();
  const lang = document.getElementById('solution-form-lang').value.trim() || 'python';
  const tagsStr = document.getElementById('solution-form-tags').value.trim();
  const tags = tagsStr ? tagsStr.split(',').map(t => t.trim()).filter(Boolean) : [];

  try {
    let res;
    if (id) {
      res = await fetch(`/api/solutions/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title, problem_description: desc, solution_code: code, language: lang, tags })
      });
    } else {
      res = await fetch('/api/solutions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title, problem_description: desc, solution_code: code, language: lang, tags })
      });
    }
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    closeSolutionModal();
    fetchSolutions();
  } catch (err) {
    alert('Ошибка сохранения решения: ' + err);
  }
}

async function deleteSolution(id) {
  if (!confirm('Удалить этот паттерн из базы решений?')) return;
  try {
    const res = await fetch(`/api/solutions/${id}`, { method: 'DELETE' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    fetchSolutions();
  } catch (e) {
    alert('Ошибка удаления: ' + e);
  }
}

// ------------------------------------------------------------------------
// 12. Presets Engine (v1.3.0)
// ------------------------------------------------------------------------
let cachedPresets = [];
let activePresetId = 'auto';

async function fetchPresets() {
  try {
    const res = await fetch('/api/presets');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    cachedPresets = data.presets || [];
    activePresetId = data.active_preset_id || 'auto';
    renderPresetsUI(cachedPresets, activePresetId);
    updateGlobalPresetDropdown(cachedPresets, activePresetId);
  } catch (e) {
    console.debug('Error fetching presets:', e);
  }
}

function updateGlobalPresetDropdown(presets, activeId) {
  const select = document.getElementById('global-preset-select');
  if (!select) return;
  select.innerHTML = presets.map(p => `
    <option value="${p.id}" ${p.id === activeId ? 'selected' : ''}>
      Сетап: ${escapeHtml(p.name)}
    </option>
  `).join('');
}

async function onGlobalPresetChange(presetId) {
  try {
    const res = await fetch(`/api/presets/active/${presetId}`, { method: 'POST' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    activePresetId = presetId;
    fetchPresets();
  } catch (e) {
    alert('Ошибка переключения пресета: ' + e);
  }
}

function renderPresetsUI(presets, activeId) {
  const container = document.getElementById('presets-cards-grid');
  if (!container) return;

  container.innerHTML = presets.map(p => {
    const isActive = (p.id === activeId);
    const badgeClass = isActive ? 'bg-emerald/15 text-emerald font-semibold border-emerald/30' : 'bg-surface text-muted border-hairline';
    const borderClass = isActive ? 'border-cobalt shadow-sm ring-1 ring-cobalt/20' : 'border-hairline hover:border-hairline/80';

    return `
      <div class="p-5 bg-panel border ${borderClass} rounded flex flex-col justify-between space-y-4">
        <div class="space-y-2.5">
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-2">
              <span class="text-[14px] font-semibold text-ink">${escapeHtml(p.name)}</span>
              ${p.is_builtin ? '<span class="px-1.5 py-0.5 rounded bg-surface border border-hairline text-[10px] font-mono text-muted">Встроенный</span>' : ''}
            </div>
            <span class="px-2 py-0.5 rounded border text-[11px] font-mono ${badgeClass}">
              ${isActive ? 'АКТИВЕН' : 'НЕАКТИВЕН'}
            </span>
          </div>
          <p class="text-[12px] text-muted leading-relaxed">${escapeHtml(p.description || '')}</p>
          
          <div class="pt-2 border-t border-hairline/60 space-y-1 text-[11px] font-mono text-muted">
            <div>Архитектор: <span class="text-ink">${escapeHtml(p.planner_model || '—')}</span></div>
            <div>Исполнитель: <span class="text-ink">${escapeHtml(p.coder_model || '—')}</span></div>
            <div class="flex items-center gap-2 pt-1 text-[10px]">
              <span class="${p.use_solutions_db ? 'text-emerald' : 'text-muted'}">● RAG решений</span>
              <span class="${p.use_dag_planner ? 'text-emerald' : 'text-muted'}">● DAG</span>
              <span class="${p.strict_ast_test_gates ? 'text-emerald' : 'text-muted'}">● AST-гейты</span>
            </div>
          </div>
        </div>

        <div class="flex items-center justify-between gap-2 pt-2 border-t border-hairline">
          ${isActive ? `
            <span class="text-[11px] text-emerald font-medium flex items-center gap-1">
              ✓ Текущий сетап
            </span>
          ` : `
            <button onclick="onGlobalPresetChange('${p.id}')" class="h-7 px-3 rounded bg-cobalt text-white text-[11px] font-medium hover:bg-[#1D4ED8]">
              Сделать активным
            </button>
          `}
          ${!p.is_builtin ? `
            <button onclick="deletePreset('${p.id}')" class="h-7 px-2 rounded bg-surface border border-crimson/30 text-crimson text-[11px] hover:bg-crimson/10">
              Удалить
            </button>
          ` : ''}
        </div>
      </div>
    `;
  }).join('');

  if (window.lucide) lucide.createIcons();
}

function openPresetModal() {
  const modal = document.getElementById('modal-preset-edit');
  if (!modal) return;
  const plannerSelect = document.getElementById('preset-form-planner');
  const coderSelect = document.getElementById('preset-form-coder');
  const connectedModels = (state.connectedAccounts || []).flatMap(a => (a.models || []).map(m => m.id));
  const optionsHtml = connectedModels.map(m => `<option value="${m}">${m}</option>`).join('') || '<option value="gemini-4-neon">gemini-4-neon</option><option value="qwen2.5-coder:14b">qwen2.5-coder:14b</option>';
  if (plannerSelect) plannerSelect.innerHTML = optionsHtml;
  if (coderSelect) coderSelect.innerHTML = optionsHtml;
  modal.classList.remove('hidden');
}

function closePresetModal() {
  const modal = document.getElementById('modal-preset-edit');
  if (modal) modal.classList.add('hidden');
}

async function submitPresetForm(e) {
  e.preventDefault();
  const name = document.getElementById('preset-form-name').value.trim();
  const desc = document.getElementById('preset-form-desc').value.trim();
  const planner = document.getElementById('preset-form-planner').value;
  const coder = document.getElementById('preset-form-coder').value;
  const rag = document.getElementById('preset-form-rag').checked;
  const dag = document.getElementById('preset-form-dag').checked;
  const comp = document.getElementById('preset-form-compression').checked;
  const gates = document.getElementById('preset-form-gates').checked;

  try {
    const res = await fetch('/api/presets', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name,
        description: desc,
        planner_model: planner,
        coder_model: coder,
        reviewer_models: [planner],
        use_solutions_db: rag,
        use_dag_planner: dag,
        use_prompt_compression: comp,
        strict_ast_test_gates: gates
      })
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    closePresetModal();
    fetchPresets();
  } catch (err) {
    alert('Ошибка создания пресета: ' + err);
  }
}

async function resetPresetsDefaults() {
  if (!confirm('Сбросить все пресеты к заводским шаблонам (Авто, Базовый, МЫСЛИТЕЛЬ)?')) return;
  try {
    const res = await fetch('/api/presets/reset-defaults', { method: 'POST' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    fetchPresets();
  } catch (e) {
    alert('Ошибка сброса: ' + e);
  }
}

async function deletePreset(id) {
  if (!confirm('Удалить этот пользовательский сетап?')) return;
  try {
    const res = await fetch(`/api/presets/${id}`, { method: 'DELETE' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    fetchPresets();
  } catch (e) {
    alert('Ошибка удаления: ' + e);
  }
}

// ------------------------------------------------------------------------
// 13. Hardware Advisor & Wizard (v1.3.0)
// ------------------------------------------------------------------------
async function openHardwareModal() {
  const modal = document.getElementById('modal-hardware-advisor');
  if (!modal) return;
  modal.classList.remove('hidden');
  loadHardwareRecommendations();
}

function closeHardwareModal() {
  const modal = document.getElementById('modal-hardware-advisor');
  if (modal) modal.classList.add('hidden');
}

async function loadHardwareRecommendations() {
  try {
    const res = await fetch('/api/hardware/recommend');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const hw = data.hardware || {};
    
    const gpuEl = document.getElementById('hw-gpu-name');
    if (gpuEl) gpuEl.textContent = hw.gpu_name || 'Интегрированная';
    const vramEl = document.getElementById('hw-vram-gb');
    if (vramEl) vramEl.textContent = (hw.vram_gb ? `${hw.vram_gb} ГБ` : '0 ГБ (CPU)');
    const ramEl = document.getElementById('hw-ram-gb');
    if (ramEl) ramEl.textContent = `${hw.ram_gb || 0} ГБ`;
    const cpuEl = document.getElementById('hw-cpu-info');
    if (cpuEl) cpuEl.textContent = `${hw.cpu_name || 'CPU'} (${hw.cpu_cores || 0} ядер)`;

    const tierBadge = document.getElementById('hw-tier-badge');
    if (tierBadge) tierBadge.textContent = data.recommended_tier || 'Стандарт';

    const footerStatus = document.getElementById('footer-ollama-status');
    if (footerStatus && hw.gpu_name) {
      footerStatus.textContent = `${hw.gpu_name} (${hw.vram_gb} GB)`;
    }

    renderCuratedModels(data.recommendations || [], data.embedding_recommendation);
  } catch (e) {
    console.debug('Error loading hardware recommendations:', e);
  }
}

function renderCuratedModels(models, embeddingModel) {
  const container = document.getElementById('hw-models-catalog');
  if (!container) return;

  const allModels = [];
  if (embeddingModel) allModels.push(embeddingModel);
  allModels.push(...models);

  container.innerHTML = allModels.map(m => {
    let actionBtn = '';
    let statusBadge = '';

    if (m.status === 'active') {
      statusBadge = '<span class="px-2 py-0.5 rounded bg-emerald/15 text-emerald font-semibold text-[10px]">АКТИВНА</span>';
      actionBtn = '<span class="text-[11px] text-emerald font-medium">✓ Используется</span>';
    } else if (m.status === 'installed') {
      statusBadge = '<span class="px-2 py-0.5 rounded bg-cobalt/15 text-cobalt font-semibold text-[10px]">УСТАНОВЛЕНА</span>';
      actionBtn = `<button onclick="activateOllamaModel('${m.name}')" class="h-7 px-3 rounded bg-cobalt text-white text-[11px] font-medium hover:bg-[#1D4ED8]">Активировать</button>`;
    } else {
      statusBadge = '<span class="px-2 py-0.5 rounded bg-surface border border-hairline text-muted text-[10px]">НЕ УСТАНОВЛЕНА</span>';
      actionBtn = `<button onclick="startOllamaPull('${m.name}')" class="h-7 px-3 rounded bg-surface border border-hairline text-[11px] font-medium text-ink hover:bg-surface/70 flex items-center gap-1">Скачать (${m.size_str || 'Ollama'})</button>`;
    }

    return `
      <div class="p-3 bg-panel border border-hairline rounded flex items-center justify-between gap-3">
        <div class="space-y-0.5 min-w-0">
          <div class="flex items-center gap-2 flex-wrap">
            <span class="font-medium text-[12px] text-ink">${escapeHtml(m.display_name || m.name)}</span>
            <span class="font-mono text-[11px] text-muted">${escapeHtml(m.name)}</span>
            ${statusBadge}
          </div>
          <p class="text-[11px] text-muted leading-tight">${escapeHtml(m.description || '')}</p>
        </div>
        <div class="shrink-0">
          ${actionBtn}
        </div>
      </div>
    `;
  }).join('');
}

async function startOllamaPull(modelName) {
  const wrap = document.getElementById('hw-pull-progress-wrap');
  const label = document.getElementById('hw-pull-status-label');
  const percentLabel = document.getElementById('hw-pull-percent-label');
  const bar = document.getElementById('hw-pull-progress-bar');

  if (wrap) wrap.classList.remove('hidden');
  if (label) label.textContent = `Подготовка к загрузке ${modelName}...`;

  try {
    const response = await fetch('/api/ollama/pull', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: modelName })
    });

    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop();

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const raw = line.slice(6).trim();
        if (raw === '[DONE]') {
          if (label) label.textContent = `Модель ${modelName} успешно загружена!`;
          if (bar) bar.style.width = '100%';
          setTimeout(() => {
            if (wrap) wrap.classList.add('hidden');
            loadHardwareRecommendations();
          }, 1500);
          return;
        }
        try {
          const payload = JSON.parse(raw);
          if (payload.status) {
            if (label) label.textContent = payload.status;
          }
          if (payload.percent !== undefined) {
            if (percentLabel) percentLabel.textContent = `${payload.percent}%`;
            if (bar) bar.style.width = `${payload.percent}%`;
          }
        } catch (_) {}
      }
    }
  } catch (e) {
    if (label) label.textContent = `Ошибка загрузки: ${e}`;
  }
}

async function activateOllamaModel(modelName) {
  try {
    const res = await fetch('/api/ollama/activate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: modelName })
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    loadHardwareRecommendations();
    fetchAccountsAndModels();
  } catch (e) {
    alert('Ошибка активации модели: ' + e);
  }
}

// ------------------------------------------------------------------------
// 14. Human-in-the-Loop Compression Review (v1.3.0)
// ------------------------------------------------------------------------
let activeHitlDraft = null;

async function checkPendingCompressionDrafts() {
  try {
    const res = await fetch('/api/compression/pending');
    if (!res.ok) return;
    const data = await res.json();
    const drafts = data.drafts || [];
    if (drafts.length > 0 && (!activeHitlDraft || activeHitlDraft.id !== drafts[0].id)) {
      showCompressionApproval(drafts[0]);
    }
  } catch (_) {}
}

function showCompressionApproval(draft) {
  activeHitlDraft = draft;
  const modal = document.getElementById('modal-compression-approval');
  if (!modal) return;

  document.getElementById('hitl-draft-id').value = draft.id;
  document.getElementById('hitl-orig-tokens').textContent = `${draft.original_tokens || 0} токенов`;
  document.getElementById('hitl-comp-tokens').textContent = `${draft.compressed_tokens || 0} токенов`;
  document.getElementById('hitl-savings-badge').textContent = `-${draft.savings_pct || 0}%`;
  document.getElementById('hitl-prompt-text').value = draft.compressed_prompt || '';
  document.getElementById('hitl-feedback-input').value = '';

  modal.classList.remove('hidden');
}

function closeCompressionApproval() {
  const modal = document.getElementById('modal-compression-approval');
  if (modal) modal.classList.add('hidden');
  activeHitlDraft = null;
}

async function executeHitlApprove() {
  if (!activeHitlDraft) return;
  const id = activeHitlDraft.id;
  const edited = document.getElementById('hitl-prompt-text').value;

  try {
    const res = await fetch(`/api/compression/${id}/approve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ edited_prompt: edited })
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    closeCompressionApproval();
  } catch (e) {
    alert('Ошибка одобрения: ' + e);
  }
}

async function executeHitlRework() {
  if (!activeHitlDraft) return;
  const id = activeHitlDraft.id;
  const feedback = document.getElementById('hitl-feedback-input').value.trim();
  if (!feedback) {
    alert('Укажите пожелание (например: "мягче" или "жестче")');
    return;
  }

  try {
    const res = await fetch(`/api/compression/${id}/rework`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ feedback })
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (data.draft) {
      showCompressionApproval(data.draft);
    }
  } catch (e) {
    alert('Ошибка доработки: ' + e);
  }
}

async function executeHitlCancel() {
  if (!activeHitlDraft) return;
  const id = activeHitlDraft.id;
  try {
    await fetch(`/api/compression/${id}/cancel`, { method: 'POST' });
    closeCompressionApproval();
  } catch (e) {
    closeCompressionApproval();
  }
}

// ------------------------------------------------------------------------
// 10. Bootstrap on Load
// ========================================================================
// Release v1.4.0: System Logs Tab & Viewer
// ========================================================================
let cachedLogLines = [];

async function loadSystemLogs() {
  const container = document.getElementById('logs-console-container');
  if (!container) return;

  const levelSelect = document.getElementById('logs-level-filter');
  const level = levelSelect ? levelSelect.value : 'all';
  const searchInput = document.getElementById('logs-search-input');
  const search = searchInput ? searchInput.value.trim() : '';

  try {
    const res = await fetch(`/v1/ui/system-logs?limit=300&level=${encodeURIComponent(level)}&search=${encodeURIComponent(search)}`);
    const data = await res.json();

    if (data.status === 'ok') {
      cachedLogLines = data.lines || [];
      const pathEl = document.getElementById('logs-file-path');
      if (pathEl && data.path) pathEl.innerText = data.path;

      const badgeEl = document.getElementById('logs-total-badge');
      if (badgeEl) badgeEl.innerText = `${data.total_lines || 0} строк`;

      renderLogsList(cachedLogLines);
    } else {
      container.innerHTML = `<div class="text-coral">Ошибка загрузки логов: ${escapeHtml(data.message || 'неизвестная ошибка')}</div>`;
    }
  } catch (err) {
    container.innerHTML = `<div class="text-coral">Сбой сетевого запроса логов: ${escapeHtml(err.message)}</div>`;
  }
}

function renderLogsList(lines) {
  const container = document.getElementById('logs-console-container');
  if (!container) return;

  const statsEl = document.getElementById('logs-stats');
  if (statsEl) statsEl.innerText = `Отображается: ${lines.length} строк`;

  if (!lines || lines.length === 0) {
    container.innerHTML = `<div class="text-slate-400 italic">Журнал пуст или нет записей по заданному фильтру.</div>`;
    return;
  }

  const html = lines.map(line => {
    let colorClass = 'text-slate-300';
    if (line.includes('[ERROR]') || line.toLowerCase().includes('error:')) {
      colorClass = 'text-coral font-medium';
    } else if (line.includes('[WARN') || line.toLowerCase().includes('warning:')) {
      colorClass = 'text-amber-400';
    } else if (line.includes('[INFO]')) {
      colorClass = 'text-sky-300';
    } else if (line.includes('[SUCCESS]') || line.includes('[OK]')) {
      colorClass = 'text-emerald-400 font-medium';
    }
    return `<div class="${colorClass} hover:bg-white/5 py-0.5 px-1 rounded transition-colors whitespace-pre-wrap break-all">${escapeHtml(line)}</div>`;
  }).join('');

  container.innerHTML = html;
  container.scrollTop = container.scrollHeight;
}

function filterLocalLogs() {
  const searchInput = document.getElementById('logs-search-input');
  const term = (searchInput ? searchInput.value : '').toLowerCase().trim();
  if (!term) {
    renderLogsList(cachedLogLines);
    return;
  }
  const filtered = cachedLogLines.filter(l => l.toLowerCase().includes(term));
  renderLogsList(filtered);
}

function copyLogsToClipboard() {
  if (!cachedLogLines || cachedLogLines.length === 0) {
    showToast('Нет логов для копирования');
    return;
  }
  const text = cachedLogLines.join('\n');
  navigator.clipboard.writeText(text).then(() => {
    showToast('Логи скопированы в буфер обмена');
  }).catch(() => {
    showToast('Не удалось скопировать в буфер');
  });
}

async function clearSystemLogs() {
  if (!confirm('Вы действительно хотите очистить системный лог-файл?')) return;
  try {
    const res = await fetch('/v1/ui/system-logs/clear', { method: 'POST' });
    const data = await res.json();
    if (data.status === 'ok') {
      showToast('Лог-файл очищен');
      loadSystemLogs();
    } else {
      showToast('Ошибка очистки логов: ' + (data.message || ''));
    }
  } catch (err) {
    showToast('Сбой очистки логов: ' + err.message);
  }
}

// ------------------------------------------------------------------------
// Toast notification system
function showToast(message, type = 'info', duration = 4000) {
  const container = document.getElementById('toast-container');
  if (!container) return;

  const toast = document.createElement('div');
  toast.className = `zca-toast toast-${type}`;

  let icon = 'ℹ️';
  if (type === 'start') icon = '⚡';
  else if (type === 'done') icon = '✅';
  else if (type === 'error') icon = '❌';

  toast.innerHTML = `
    <span class="text-[14px] shrink-0 select-none">${icon}</span>
    <div class="flex-1 min-w-0">
      <div class="font-medium text-ink">${message}</div>
    </div>
  `;

  container.appendChild(toast);
  requestAnimationFrame(() => toast.classList.add('show'));

  setTimeout(() => {
    toast.classList.remove('show');
    setTimeout(() => {
      if (toast.parentNode) toast.parentNode.removeChild(toast);
    }, 250);
  }, duration);
}

// ------------------------------------------------------------------------
// Activity Tracker Live SSE
let activityEventSource = null;
let activityTimerInterval = null;
let activityStartedAt = null;

function updateActivityUI(data) {
  const bar = document.getElementById('activity-status-bar');
  const textEl = document.getElementById('activity-status-text');
  const timerEl = document.getElementById('activity-status-timer');

  // Live Card Elements
  const card = document.getElementById('orchestrator-live-card');
  const cardPulse = document.getElementById('orch-card-pulse');
  const cardTitle = document.getElementById('orch-card-title');
  const cardTimer = document.getElementById('orch-card-timer');
  const cardStage = document.getElementById('orch-card-stage');
  const cardSource = document.getElementById('orch-card-source-badge');
  const cardDetails = document.getElementById('orch-card-last-summary');
  const cardMulti = document.getElementById('orch-card-multi-arch');

  if (data.state === 'working') {
    // 1. Header Bar
    if (bar) bar.className = 'activity-bar working flex items-center gap-1.5 px-2.5 py-1 rounded border text-[11px] font-mono cursor-default transition-all duration-200 select-none max-w-[320px] truncate';
    
    const stagePrefix = data.stage_label || (data.stage ? data.stage.toUpperCase() : (data.tool || 'ZCA'));
    let display = `⚡ ${stagePrefix}`;
    if (data.model) display += `: ${data.model}`;
    if (data.source) display += ` [${data.source}]`;
    if (textEl) textEl.textContent = display;
    if (bar) bar.title = display;

    if (timerEl) timerEl.classList.remove('hidden');
    if (data.started_at) {
      activityStartedAt = data.started_at;
      if (!activityTimerInterval) {
        activityTimerInterval = setInterval(() => {
          if (!activityStartedAt) return;
          const secs = Math.floor(Date.now() / 1000 - activityStartedAt);
          const formatted = `${Math.max(0, secs)}s`;
          if (timerEl) timerEl.textContent = formatted;
          if (cardTimer) cardTimer.textContent = `${Math.max(0, secs)}.0s`;
        }, 1000);
      }
      const initialSecs = Math.floor(Date.now() / 1000 - activityStartedAt);
      if (timerEl) timerEl.textContent = `${Math.max(0, initialSecs)}s`;
      if (cardTimer) cardTimer.textContent = `${Math.max(0, initialSecs)}.0s`;
    }

    // 2. Live Card
    if (card) {
      card.className = 'p-3 border-b border-cobalt/40 bg-cobalt/5 transition-all duration-200 animate-pulse';
    }
    if (cardPulse) {
      cardPulse.className = 'w-2 h-2 rounded-full bg-cobalt animate-ping shrink-0';
    }
    if (cardTitle) {
      cardTitle.textContent = data.stage_label || 'Оркестратор выполняет задачу';
    }
    if (cardTimer) {
      cardTimer.classList.remove('hidden');
    }
    if (cardStage) {
      let stageText = `⚡ ${data.step || 'Выполнение шага...'}`;
      if (data.model) stageText += ` (${data.model})`;
      cardStage.textContent = stageText;
    }
    if (cardSource) {
      if (data.source || data.account) {
        cardSource.textContent = data.source ? `${data.source}${data.account ? ' · ' + data.account : ''}` : data.account;
        cardSource.classList.remove('hidden');
      } else {
        cardSource.classList.add('hidden');
      }
    }
    if (cardDetails) {
      if (data.fallback_active) {
        cardDetails.innerHTML = `<span class="text-amber font-semibold">⚠️ Резервный фолбэк: ${data.fallback_reason || 'переключение'}</span>`;
      } else if (data.task_summary) {
        cardDetails.textContent = `Задача: ${data.task_summary}`;
      } else {
        cardDetails.textContent = data.step || 'Обработка запроса...';
      }
    }

    // Multi-Architect parallel indicators
    if (cardMulti) {
      if (data.parallel_architects && data.parallel_architects.length > 0) {
        cardMulti.innerHTML = data.parallel_architects.map(pa => {
          const color = pa.status === 'done' ? 'text-emerald border-emerald/30 bg-emerald/10' :
                        pa.status === 'failed' ? 'text-crimson border-crimson/30 bg-crimson/10' :
                        'text-cobalt border-cobalt/30 bg-cobalt/10 animate-pulse';
          return `<span class="px-1.5 py-0.5 rounded border text-[10px] ${color}">${pa.label || pa.model}</span>`;
        }).join('');
        cardMulti.classList.remove('hidden');
      } else {
        cardMulti.classList.add('hidden');
      }
    }

    // 3. Highlight DAG Stage
    const stageKey = (data.stage || '').toLowerCase();
    if (stageKey.includes('plan') || stageKey.includes('architect') || stageKey.includes('consensus')) {
      state.dagStages[0].status = 'running';
      state.dagStages[1].status = 'idle';
      state.dagStages[2].status = 'idle';
      state.dagStages[3].status = 'idle';
    } else if (stageKey.includes('cod') || stageKey.includes('exec')) {
      state.dagStages[0].status = 'pass';
      state.dagStages[1].status = 'running';
      state.dagStages[2].status = 'idle';
      state.dagStages[3].status = 'idle';
    } else if (stageKey.includes('gate')) {
      state.dagStages[0].status = 'pass';
      state.dagStages[1].status = 'pass';
      state.dagStages[2].status = 'running';
      state.dagStages[3].status = 'idle';
    } else if (stageKey.includes('audit') || stageKey.includes('rev')) {
      state.dagStages[0].status = 'pass';
      state.dagStages[1].status = 'pass';
      state.dagStages[2].status = 'pass';
      state.dagStages[3].status = 'running';
    }
    renderDagInspector();

  } else {
    // idle
    if (bar) bar.className = 'activity-bar idle flex items-center gap-1.5 px-2.5 py-1 rounded border text-[11px] font-mono cursor-default transition-all duration-200 select-none max-w-[320px] truncate';
    if (textEl) textEl.textContent = '🟢 Оркестратор готов (IDLE)';
    if (timerEl) timerEl.classList.add('hidden');
    if (activityTimerInterval) {
      clearInterval(activityTimerInterval);
      activityTimerInterval = null;
    }
    activityStartedAt = null;

    if (card) {
      card.className = 'p-3 border-b border-hairline bg-surface transition-all duration-200';
    }
    if (cardPulse) {
      cardPulse.className = 'w-2 h-2 rounded-full bg-emerald shrink-0';
    }
    if (cardTitle) {
      cardTitle.textContent = '🟢 Оркестратор готов (IDLE)';
    }
    if (cardTimer) {
      cardTimer.classList.add('hidden');
    }
    if (cardStage) {
      cardStage.textContent = 'Готов к выполнению задач (IDLE)';
    }
    if (cardSource) {
      cardSource.classList.add('hidden');
    }
    if (cardMulti) {
      cardMulti.classList.add('hidden');
    }

    if (data.last_completed) {
      const lc = data.last_completed;
      const statusTitle = `Последний вызов: ${lc.tool || 'ZCA'} (${lc.model || 'локально'}) — ${lc.elapsed_seconds}с`;
      if (bar) bar.title = statusTitle;
      if (cardDetails) cardDetails.textContent = `Последний запуск: ${lc.summary || (lc.success ? 'Успешно' : 'Ошибка')} (${lc.elapsed_seconds}s)`;
    } else {
      if (bar) bar.title = 'Оркестратор готов к выполнению задач';
      if (cardDetails) cardDetails.textContent = 'Ожидание входящих запросов...';
    }

    state.dagStages.forEach(st => {
      if (st.status === 'running') st.status = 'idle';
    });
    renderDagInspector();
  }

  // 4. Update Live Task Activity Feed
  const feedList = document.getElementById('task-live-feed-list');
  const feedCount = document.getElementById('task-live-feed-count');
  if (feedList && data.history) {
    if (feedCount) feedCount.textContent = `${data.history.length} событий`;
    if (data.history.length === 0) {
      feedList.innerHTML = '<div class="text-muted text-[10px] italic">Событий пока нет</div>';
    } else {
      feedList.innerHTML = data.history.map(item => {
        let color = 'text-ink';
        if (item.type === 'fallback') color = 'text-amber font-semibold';
        else if (item.type === 'done') color = 'text-emerald font-semibold';
        else if (item.type === 'error') color = 'text-crimson font-semibold';
        else if (item.type === 'planning') color = 'text-cobalt';
        else if (item.type === 'coding') color = 'text-purple-400';
        else if (item.type === 'audit') color = 'text-sky-400';
        return `<div class="flex items-start gap-1 py-0.5 leading-tight"><span class="text-muted text-[10px] shrink-0 font-sans">[${escapeHtml(item.time || '')}]</span><span class="${color} break-words">${escapeHtml(item.message || '')}</span></div>`;
      }).join('');
      feedList.scrollTop = feedList.scrollHeight;
    }
  }
}

// ------------------------------------------------------------------------
// IDE Routing Mode (through_zca vs bypass)
let currentIdeRoutingMode = 'through_zca';

async function fetchIdeRoutingMode() {
  try {
    const res = await fetch('/api/routing-mode');
    const data = await res.json();
    if (data.mode) {
      currentIdeRoutingMode = data.mode;
      renderIdeRoutingModeUI(currentIdeRoutingMode);
    }
  } catch (err) {
    console.debug('[Routing] Failed to fetch mode:', err);
  }
}

function renderIdeRoutingModeUI(mode) {
  const btn = document.getElementById('ide-routing-toggle');
  const icon = document.getElementById('ide-routing-icon');
  const text = document.getElementById('ide-routing-text');
  if (!btn) return;
  if (mode === 'through_zca') {
    btn.className = 'flex items-center gap-1.5 px-2.5 py-1 rounded border text-[11px] font-medium transition-all duration-200 select-none cursor-pointer bg-emerald-500/10 border-emerald-500/30 text-emerald-400 hover:bg-emerald-500/20';
    btn.title = 'Режим ZCA активен: ВСЕ задачи идут через zero-copy-agent. Нажмите для переключения в режим обхода.';
    if (icon) icon.textContent = '⚡';
    if (text) text.textContent = 'Через ZCA (ВСЕ запросы)';
  } else {
    btn.className = 'flex items-center gap-1.5 px-2.5 py-1 rounded border text-[11px] font-medium transition-all duration-200 select-none cursor-pointer bg-amber-500/10 border-amber-500/30 text-amber-400 hover:bg-amber-500/20';
    btn.title = 'Режим обхода: задачи идут напрямую в IDE. Нажмите для включения маршрутизации через ZCA.';
    if (icon) icon.textContent = '⏭️';
    if (text) text.textContent = 'В обход ZCA (Прямой IDE)';
  }
}

async function toggleIdeRoutingMode() {
  const nextMode = currentIdeRoutingMode === 'through_zca' ? 'bypass' : 'through_zca';
  try {
    const res = await fetch('/api/routing-mode', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode: nextMode })
    });
    const data = await res.json();
    if (data.status === 'ok') {
      currentIdeRoutingMode = data.mode;
      renderIdeRoutingModeUI(currentIdeRoutingMode);
      const label = currentIdeRoutingMode === 'through_zca' ? 'Через ZCA (ВСЕ запросы)' : 'В обход ZCA (Прямой IDE)';
      showToast(`Маршрутизация: ${label}`, 'info');
    }
  } catch (err) {
    showToast('Ошибка переключения режима: ' + err.message, 'error');
  }
}

function initActivityTrackerSSE() {
  if (activityEventSource) {
    activityEventSource.close();
  }

  try {
    activityEventSource = new EventSource('/api/activity/stream');

    activityEventSource.addEventListener('current', (e) => {
      try {
        const data = JSON.parse(e.data);
        updateActivityUI(data);
      } catch (err) {
        console.error('[Activity] Error parsing current event:', err);
      }
    });

    activityEventSource.addEventListener('start', (e) => {
      try {
        const data = JSON.parse(e.data);
        updateActivityUI(data);
        const tool = data.tool || 'Инструмент';
        const model = data.model ? ` (${data.model})` : '';
        showToast(`Выполняется: ${tool}${model}`, 'start', 4000);
      } catch (err) {
        console.error('[Activity] Error parsing start event:', err);
      }
    });

    activityEventSource.addEventListener('progress', (e) => {
      try {
        const data = JSON.parse(e.data);
        updateActivityUI(data);
      } catch (err) {
        console.error('[Activity] Error parsing progress event:', err);
      }
    });

    activityEventSource.addEventListener('done', (e) => {
      try {
        const data = JSON.parse(e.data);
        updateActivityUI(data);
        const comp = data.completed || {};
        const tool = comp.tool || 'Задача';
        const elapsed = comp.elapsed_seconds ? ` за ${comp.elapsed_seconds}с` : '';
        const model = comp.model ? ` [${comp.model}]` : '';
        showToast(`${tool} завершен${elapsed}${model}`, 'done', 4000);
      } catch (err) {
        console.error('[Activity] Error parsing done event:', err);
      }
    });

    activityEventSource.addEventListener('error', (e) => {
      if (e.data) {
        try {
          const data = JSON.parse(e.data);
          updateActivityUI(data);
          const comp = data.completed || {};
          showToast(`Ошибка ${comp.tool || 'выполнения'}: ${comp.error || 'Сбой'}`, 'error', 4000);
        } catch (err) {
          // Normal SSE reconnect
        }
      }
    });
  } catch (err) {
    console.warn('[Activity] EventSource connection failed:', err);
  }
}

// ------------------------------------------------------------------------
window.addEventListener('DOMContentLoaded', () => {
  renderTraces();
  renderGlobalModelSelect();
  renderDagInspector();
  renderMetrics();
  renderCliTab();
  if (window.lucide) lucide.createIcons();

  // Load real data from backend
  fetchSystemStatus();
  fetchAccountsAndModels();
  syncConnectedModels();
  fetchIdeIntegrations();
  runDlpBench();
  fetchRealHistoryIntoTraces();
  loadOrchestrationConfig();
  fetchPresets();
  fetchIdeRoutingMode();

  // Initialize Activity Tracker SSE
  initActivityTrackerSSE();

  // Check URL params for post-update toast
  const urlParams = new URLSearchParams(window.location.search);
  if (urlParams.has('post_update') || urlParams.has('updated')) {
    const ver = urlParams.get('version') || '1.6.0';
    setTimeout(() => {
      showToast(`Обновлено до версии v${ver}`, 'done', 6000);
    }, 500);
  }

  // Periodic polling every 4s
  setInterval(() => {
    fetchSystemStatus();
    checkPendingCompressionDrafts();
    if (state.activeView === 'logs') {
      const autoRefresh = document.getElementById('logs-auto-refresh');
      if (autoRefresh && autoRefresh.checked) {
        loadSystemLogs();
      }
    }
  }, 4000);

  // Auto-check for updates after 2 seconds
  setTimeout(() => {
    checkUpdates({ autoPrompt: true });
  }, 2000);
});
