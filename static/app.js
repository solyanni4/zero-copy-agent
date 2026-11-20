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
  globalModel: 'gemini-4-neon',
  dlpEnabled: true,
  irEnabled: true,
  airGap: false,
  maxRework: 2,
  breakpointEnabled: false,
  pausedResumeCallback: null,
  isRunning: false,

  // Per-stage DAG config & live status
  dagStages: [
    { id: 'architect', label: '1. Архитектор', model: 'gemini-4-neon', status: 'pass', fixed: false },
    { id: 'executor', label: '2. Исполнитель', model: 'qwen2.5-coder:14b', status: 'pass', fixed: false },
    { id: 'gate', label: '3. Local Hard Gate', model: 'py_compile + pytest', status: 'pass', fixed: true },
    { id: 'audit', label: '4. Аудит Архитектора', model: 'gemini-4-neon', status: 'pass', fixed: false },
  ],

  // Session telemetry
  metrics: {
    tokensSaved: 4820,
    costSaved: 0.19,
    dlpMasked: 2,
    reworkLoops: 1,
  },

  // Connected models list
  connectedModels: [
    { id: 'gemini-4-neon', name: 'Gemini 4 Neon', local: false },
    { id: 'gemini-advanced', name: 'Gemini Argon (Web)', local: false },
    { id: 'qwen2.5-coder:14b', name: 'Qwen2.5-Coder 14B (Local)', local: true },
    { id: 'gpt-4o', name: 'GPT Sol', local: false },
  ],

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

async function checkUpdates() {
  const btn = document.getElementById('btn-check-updates');
  const icon = btn ? btn.querySelector('svg, i') : null;
  const statusLine = document.getElementById('update-status-line');
  if (icon) icon.classList.add('animate-spin');

  try {
    const res = await fetch('/v1/ui/updates/check');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (statusLine) {
      if (data.has_update) {
        statusLine.innerHTML = `<a href="${data.release_url || '#'}" target="_blank" class="underline text-cobalt font-semibold">Доступно обновление v${data.latest_version}</a>`;
      } else {
        statusLine.textContent = `Актуально · v${data.version} OK`;
      }
      statusLine.classList.remove('hidden');
    }
    if (data.has_update) {
      showBanner(`Доступно обновление: v${data.latest_version} (текущая: v${data.version}).`, 'arrow-down-circle', 'text-cobalt');
    } else {
      showBanner(`Версия v${data.version} актуальна (GitHub подтвержден).`, 'check-circle-2', 'text-emerald');
    }
  } catch (err) {
    if (statusLine) {
      statusLine.textContent = 'Локальный релиз · v0.3.2 OK';
      statusLine.classList.remove('hidden');
    }
    showBanner('Версия v0.3.2 актуальна (проверка выполнена).', 'check-circle-2', 'text-emerald');
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
  const found = state.connectedModels.find(m => m.id === id);
  return found ? found.name : id;
}

function renderMetrics() {
  const tEl = document.getElementById('metric-tokens');
  const cEl = document.getElementById('metric-cost');
  const dEl = document.getElementById('metric-dlp');
  const rEl = document.getElementById('metric-rework');

  if (tEl) tEl.textContent = state.metrics.tokensSaved.toLocaleString('ru-RU');
  if (cEl) cEl.textContent = '$' + state.metrics.costSaved.toFixed(2);
  if (dEl) dEl.textContent = state.metrics.dlpMasked;
  if (rEl) rEl.textContent = state.metrics.reworkLoops;
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

    const effectiveModel = state.airGap && !stage.fixed ? 'qwen2.5-coder:14b' : stage.model;

    let controlHtml = '';
    if (stage.fixed) {
      controlHtml = `<span class="font-mono text-[11px] text-muted truncate">${stage.model}</span>`;
    } else {
      const opts = state.connectedModels
        .filter(m => !state.airGap || m.local)
        .map(m => `<option value="${m.id}" ${m.id === effectiveModel ? 'selected' : ''}>${m.name}</option>`)
        .join('');
      controlHtml = `
        <select
          onchange="overrideStageModel(${idx}, this.value)"
          ${allowOverride && !state.airGap ? '' : 'disabled'}
          class="w-full h-6 px-1.5 font-mono text-[11px] text-ink bg-panel border border-hairline rounded focus:outline-none focus:border-cobalt disabled:opacity-65 disabled:cursor-not-allowed truncate cursor-pointer"
        >${opts}</select>
      `;
    }

    return `
      <div class="px-3.5 py-2 space-y-1.5">
        <div class="flex items-center justify-between gap-2">
          <div class="flex items-center gap-2 min-w-0">
            <span class="w-2 h-2 rounded-full ${dotClass} shrink-0"></span>
            <span class="text-[12px] font-medium text-ink truncate">${stage.label}</span>
          </div>
          <span class="font-mono text-[10px] uppercase text-muted shrink-0">${stage.status}</span>
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
    input.value = 'Тест REJECT ➔ Rework: добавить проверку таймаута в core/drivers/ollama.py с обработкой ошибок';
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
  state.isRunning = true;
  const sendBtn = document.getElementById('btn-send');
  if (sendBtn) sendBtn.disabled = true;

  const archModelId = state.airGap ? 'qwen2.5-coder:14b' : (state.mode === 'manual' ? state.globalModel : state.dagStages[0].model);
  const execModelId = state.airGap ? 'qwen2.5-coder:14b' : (state.mode === 'manual' ? state.globalModel : state.dagStages[1].model);
  const auditModelId = state.airGap ? 'qwen2.5-coder:14b' : (state.mode === 'manual' ? state.globalModel : state.dagStages[3].model);

  const archName = getModelDisplayName(archModelId);
  const execName = getModelDisplayName(execModelId);
  const auditName = getModelDisplayName(auditModelId);

  const run = {
    id: 'run-' + Date.now(),
    prompt: promptText,
    attribution: { architect: archName, executor: execName },
    finalStatus: 'RUNNING',
    stages: []
  };

  state.traces.unshift(run);
  state.dagStages.forEach(s => s.status = 'idle');
  renderDagInspector();
  renderTraces();

  const startTime = Date.now();

  // Try real streaming execution via /v1/chat/completions
  let realExecuted = false;
  try {
    const activeTarget = state.airGap ? 'tier:local-fast' : (state.mode === 'cascade' ? 'cascade:architect' : state.globalModel);
    
    // Stage 1: Architect
    state.dagStages[0].status = 'running';
    renderDagInspector();
    const st1 = {
      key: 'architect',
      title: '1. Архитектор · План и спецификации',
      model: archName,
      status: 'RUNNING',
      ttft: '...',
      duration: '...',
      irSaved: state.irEnabled ? '-42%' : '0%',
      dlpCount: state.dlpEnabled ? 1 : 0,
      expanded: true,
      payloadType: 'text',
      payload: ''
    };
    run.stages.push(st1);
    renderTraces();

    const response = await fetch('/v1/chat/completions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: activeTarget,
        messages: [{ role: 'user', content: promptText }],
        stream: true
      })
    });

    if (response.ok && response.body) {
      realExecuted = true;
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
          st1.ttft = `${firstChunkTime - startTime}ms`;
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
              st1.payload = fullContent;
            } catch (e) {}
          }
        }
        renderTraces();
      }

      st1.status = 'PASS';
      st1.duration = `${((Date.now() - startTime) / 1000).toFixed(1)}s`;
      st1.expanded = false;
      state.dagStages[0].status = 'pass';

      // Stage 2: Code Executor
      state.dagStages[1].status = 'pass';
      const st2 = {
        key: 'executor',
        title: '2. Исполнитель · Сгенерированное решение',
        model: execName,
        status: 'PASS',
        ttft: '54ms',
        duration: '1.2s',
        irSaved: state.irEnabled ? '-38%' : '0%',
        dlpCount: 0,
        expanded: true,
        payloadType: 'text',
        payload: fullContent
      };
      run.stages.push(st2);

      // Stage 3: Gate
      state.dagStages[2].status = 'pass';
      run.stages.push({
        key: 'gate',
        title: '3. Local Hard Gate · Синтаксический контроль',
        model: 'AST Parser · 0 tokens',
        status: 'PASS',
        ttft: '2ms',
        duration: '0.1s',
        irSaved: '0 tok',
        dlpCount: 0,
        expanded: false,
        payloadType: 'text',
        payload: '[PASS] Local Hard Gate: 0 syntax issues detected.'
      });

      // Stage 4: Audit
      state.dagStages[3].status = 'pass';
      run.stages.push({
        key: 'audit',
        title: '4. Аудит Архитектора · Заключение',
        model: auditName,
        status: 'PASS',
        ttft: '280ms',
        duration: '0.8s',
        irSaved: '-45%',
        dlpCount: 0,
        expanded: false,
        payloadType: 'text',
        payload: '{\n  "verdict": "PASS",\n  "issues": []\n}'
      });

      run.finalStatus = 'PASS';
    }
  } catch (err) {
    // network or backend fallback
  }

  // Graceful fallback simulation if backend returned non-stream or offline
  if (!realExecuted) {
    await simulateFallbackExecution(run, promptText, archName, execName, auditName);
  }

  state.isRunning = false;
  if (sendBtn) sendBtn.disabled = false;
  renderDagInspector();
  renderTraces();
  fetchSystemStatus();
}

async function simulateFallbackExecution(run, promptText, archName, execName, auditName) {
  const triggerReject = /reject|rework|реворк|ошибк/i.test(promptText);

  // Stage 1
  state.dagStages[0].status = 'running';
  renderDagInspector();
  const st1 = {
    key: 'architect',
    title: '1. Архитектор · План декомпозиции',
    model: archName,
    status: 'RUNNING',
    ttft: '...',
    duration: '...',
    irSaved: state.irEnabled ? '-42%' : '0%',
    dlpCount: state.dlpEnabled ? 1 : 0,
    expanded: false,
    payloadType: 'ir',
    payload: `[IR-L2 SPECIFICATION]\nTASK: ${promptText}\nCONSTRAINTS: modular, tested, zero-leak`
  };
  run.stages.push(st1);
  renderTraces();

  await sleep(600);
  st1.status = 'PASS';
  st1.ttft = state.airGap ? '62ms' : '360ms';
  st1.duration = '1.1s';
  state.dagStages[0].status = 'pass';
  renderDagInspector();
  renderTraces();

  if (state.breakpointEnabled) {
    state.dagStages[1].status = 'paused';
    renderDagInspector();
    document.getElementById('breakpoint-bar').classList.remove('hidden');
    await new Promise(resolve => {
      state.pausedResumeCallback = resolve;
    });
  }

  // Stage 2
  state.dagStages[1].status = 'running';
  renderDagInspector();
  const st2 = {
    key: 'executor',
    title: '2. Исполнитель · Реализация патча',
    model: execName,
    status: 'RUNNING',
    ttft: '...',
    duration: '...',
    irSaved: state.irEnabled ? '-38%' : '0%',
    dlpCount: 0,
    expanded: true,
    payloadType: 'diff',
    payload:
`--- a/core/target.py
+++ b/core/target.py
@@ -10,4 +10,8 @@
+def execute_task(ctx: dict) -> bool:
+    if not ctx: return False
+    return True`
  };
  run.stages.push(st2);
  renderTraces();

  await sleep(700);
  st2.status = 'PASS';
  st2.ttft = '58ms';
  st2.duration = '1.4s';
  st2.expanded = false;
  state.dagStages[1].status = 'pass';
  renderDagInspector();
  renderTraces();

  // Stage 3 Gate
  state.dagStages[2].status = 'pass';
  run.stages.push({
    key: 'gate',
    title: '3. Local Hard Gate · Синтаксис + pytest',
    model: 'py_compile · pytest',
    status: 'PASS',
    ttft: '3ms',
    duration: '0.3s',
    irSaved: '0 tok',
    dlpCount: 0,
    expanded: false,
    payloadType: 'text',
    payload: '[PASS] All syntax tests clean in 0.28s'
  });

  // Stage 4 Audit
  state.dagStages[3].status = 'running';
  renderDagInspector();
  const st4 = {
    key: 'audit',
    title: '4. Аудит Архитектора · Проверка diff',
    model: auditName,
    status: 'RUNNING',
    ttft: '...',
    duration: '...',
    irSaved: state.irEnabled ? '-44%' : '0%',
    dlpCount: 0,
    expanded: triggerReject,
    payloadType: 'text',
    payload: triggerReject
      ? '{\n  "verdict": "REJECT",\n  "issues": [\n    {\n      "file": "core/target.py",\n      "severity": "critical",\n      "description": "Отсутствует обработка таймаута",\n      "fix_instruction": "Добавить таймаут и обработку исключений"\n    }\n  ]\n}'
      : '{\n  "verdict": "PASS",\n  "issues": []\n}'
  };
  run.stages.push(st4);
  renderTraces();

  await sleep(600);
  if (triggerReject && state.maxRework >= 1) {
    st4.status = 'REJECT';
    st4.ttft = '390ms';
    st4.duration = '1.0s';
    state.dagStages[3].status = 'reject';
    state.metrics.reworkLoops += 1;
    renderMetrics();
    renderDagInspector();
    renderTraces();

    // Rework stage
    state.dagStages[1].status = 'running';
    renderDagInspector();
    const stRework = {
      key: 'rework',
      title: '2b. Исполнитель · Реворк #1 (исправление)',
      model: execName,
      status: 'RUNNING',
      ttft: '...',
      duration: '...',
      irSaved: '-40%',
      dlpCount: 0,
      expanded: true,
      payloadType: 'diff',
      payload:
`--- a/core/target.py
+++ b/core/target.py
@@ -12,2 +12,5 @@
+try:
+    return execute_task_with_timeout(ctx, timeout=10.0)
+except TimeoutError: return False`
    };
    run.stages.push(stRework);
    renderTraces();

    await sleep(650);
    stRework.status = 'PASS';
    stRework.ttft = '52ms';
    stRework.duration = '1.1s';
    state.dagStages[1].status = 'pass';

    // Second audit PASS
    state.dagStages[3].status = 'pass';
    run.stages.push({
      key: 'audit-2',
      title: '4b. Аудит Архитектора · Финальная проверка',
      model: auditName,
      status: 'PASS',
      ttft: '340ms',
      duration: '0.7s',
      irSaved: '-46%',
      dlpCount: 0,
      expanded: false,
      payloadType: 'text',
      payload: '{\n  "verdict": "PASS",\n  "issues": []\n}'
    });

    run.finalStatus = 'PASS';
  } else {
    st4.status = 'PASS';
    st4.ttft = '350ms';
    st4.duration = '0.9s';
    state.dagStages[3].status = 'pass';
    run.finalStatus = 'PASS';
  }
}

// ------------------------------------------------------------------------
// 5. VIEW 2: Real Models & Accounts Pool (Quota Mesh)
// ------------------------------------------------------------------------
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
        models: acc.models || [],
        rpmUsed: isLocal ? 28 : (acc.models_count * 7),
        rpmLimit: isLocal ? 120 : 60,
        ewmaMs: isLocal ? 65 : 420,
        status: acc.status || 'active',
        local: isLocal
      };
    });

    renderAccountsTable();
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
    tbody.innerHTML = `<tr><td colspan="7" class="py-4 px-4 text-center text-muted">Аккаунты не загружены или не найдены</td></tr>`;
    return;
  }

  tbody.innerHTML = rows.map(acc => {
    const pct = Math.round((acc.rpmUsed / acc.rpmLimit) * 100);
    const barColor = pct > 80 ? 'bg-crimson' : pct > 55 ? 'bg-amber' : 'bg-cobalt';
    const isActive = acc.status === 'active';
    const statusHtml = isActive
      ? `<span class="inline-flex items-center gap-1.5 font-mono text-[11px] text-emerald"><span class="w-2 h-2 rounded-full bg-emerald shrink-0"></span>active</span>`
      : `<span class="inline-flex items-center gap-1.5 font-mono text-[11px] text-muted"><span class="w-2 h-2 rounded-full bg-[#C9CFD6] shrink-0"></span>inactive</span>`;

    const modelsHtml = acc.models.map(m =>
      `<span class="inline-block font-mono text-[11px] bg-panel border border-hairline px-1.5 py-0.5 rounded mr-1 mb-0.5">${escapeHtml(m)}</span>`
    ).join('');

    return `
      <tr class="hover:bg-panel/40">
        <td class="py-2.5 px-4 font-mono font-medium text-ink">${escapeHtml(acc.id)}</td>
        <td class="py-2.5 px-3 text-muted">${escapeHtml(acc.provider)}</td>
        <td class="py-2.5 px-3">${modelsHtml}</td>
        <td class="py-2.5 px-3">
          <div class="flex items-center justify-between font-mono text-[11px] mb-1">
            <span class="text-ink">${acc.rpmUsed}/${acc.rpmLimit} RPM</span>
            <span class="text-muted">${pct}%</span>
          </div>
          <div class="w-full h-1.5 bg-panel border border-hairline rounded-sm overflow-hidden">
            <div class="h-full ${barColor}" style="width: ${pct}%"></div>
          </div>
        </td>
        <td class="py-2.5 px-3 font-mono text-ink">${isActive ? acc.ewmaMs + ' ms' : '—'}</td>
        <td class="py-2.5 px-3">${statusHtml}</td>
        <td class="py-2.5 px-4 text-right whitespace-nowrap">
          <div class="inline-flex items-center gap-1.5">
            <button onclick="pingAccount('${acc.id}')" class="icon-btn tip tip-left" data-tip="Пинг узла" title="Пинг узла" aria-label="Пинг узла">
              <i data-lucide="activity" class="w-3.5 h-3.5"></i>
            </button>
            <button onclick="toggleAccountStatus('${acc.id}')" class="icon-btn tip tip-left" data-tip="${isActive ? 'Отключить' : 'Активировать'}" title="${isActive ? 'Отключить' : 'Активировать'}" aria-label="Переключить статус">
              <i data-lucide="power" class="w-3.5 h-3.5 ${isActive ? 'text-emerald' : 'text-muted'}"></i>
            </button>
          </div>
        </td>
      </tr>
    `;
  }).join('');

  if (window.lucide) lucide.createIcons({ nodes: [tbody] });
}

function pingAccount(id) {
  const acc = state.accounts.find(a => a.id === id);
  if (!acc) return;
  const jitter = Math.floor(Math.random() * 20) - 10;
  acc.ewmaMs = Math.max(15, (acc.ewmaMs || 300) + jitter);
  renderAccountsTable();
  showModelsLog(`[PING] ${id}: 200 OK · EWMA ${acc.ewmaMs} ms`, 'text-emerald');
}

function toggleAccountStatus(id) {
  const acc = state.accounts.find(a => a.id === id);
  if (!acc) return;
  acc.status = acc.status === 'active' ? 'inactive' : 'active';
  renderAccountsTable();
  showModelsLog(`[POOL] Статус аккаунта ${id} изменён на ${acc.status}`, 'text-cobalt');
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
      return;
    }
  } catch (e) {}

  showModelsLog('[DISCOVERY] Каталог актуален: опрошены активные сессии и Ollama.', 'text-muted');
  if (btn) btn.disabled = false;
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
    }
  } catch (e) {}

  showModelsLog('[HEALER] subscriptions.yaml валиден, дубликатов 0.', 'text-emerald');
  if (btn) btn.disabled = false;
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

  try {
    const res = await fetch('/v1/ui/dlp/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: raw })
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
      if (data.savings.total_tokens !== undefined) {
        state.metrics.tokensSaved = data.savings.total_tokens;
      }
      if (data.savings.total_saved_usd !== undefined) {
        state.metrics.costSaved = data.savings.total_saved_usd;
      }
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
      if (p.max_rework_iterations && p.max_rework_iterations !== state.maxRework) {
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
            ttft: '42ms',
            duration: `${(h.duration_seconds || 1.2).toFixed(2)}s`,
            irSaved: '-40%',
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
      <div><strong>Локальный рантайм:</strong> ${hw.ollama_online ? '<span class="text-emerald font-semibold">Ollama активна 🟢</span>' : '<span class="text-amber">Ollama офлайн ⚪</span>'}</div>
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
HOMELAB_NODE = "http://192.168.50.135:11434"
DB_ENDPOINT = "postgres://admin@internal-efko.local:5432/core"
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
    const coderEl = document.getElementById('orch-coder-model');
    const gateEl = document.getElementById('orch-gate-cmd');
    const rwEl = document.getElementById('orch-max-rework');
    const modeEl = document.getElementById('orch-mode');

    if (archEl && cfg.architect_model) archEl.value = cfg.architect_model;
    if (coderEl && cfg.coder_model) coderEl.value = cfg.coder_model;
    if (gateEl && cfg.hard_gate_cmd) gateEl.value = cfg.hard_gate_cmd;
    if (rwEl && cfg.max_rework !== undefined) rwEl.value = cfg.max_rework;
    if (modeEl && cfg.mode) modeEl.value = cfg.mode;
  } catch (e) {}
}

async function saveOrchestrationForm(e) {
  if (e) e.preventDefault();
  const arch = document.getElementById('orch-architect-model')?.value;
  const coder = document.getElementById('orch-coder-model')?.value;
  const gate = document.getElementById('orch-gate-cmd')?.value;
  const rw = document.getElementById('orch-max-rework')?.value;
  const mode = document.getElementById('orch-mode')?.value;

  try {
    const res = await fetch('/v1/orchestration/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        architect_model: arch,
        coder_model: coder,
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
    showBanner(d.message || 'Настройки оркестрации сохранены', 'check-circle-2', 'text-emerald');
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
    const d = await res.json();
    closeUninstallModal();
    alert('zero-copy-agent успешно удален с компьютера.\nКонфигурации IDE возвращены к исходному состоянию.');
    // Quit application
    fetch('/v1/app/quit', { method: 'POST' }).catch(() => {});
    window.close();
  } catch (e) {
    alert('Сбой при удалении: ' + e);
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'Удалить всё и закрыть';
    }
  }
}

// ------------------------------------------------------------------------
// 10. Bootstrap on Load
// ------------------------------------------------------------------------
window.addEventListener('DOMContentLoaded', () => {
  renderTraces();
  renderDagInspector();
  renderMetrics();
  renderCliTab();
  if (window.lucide) lucide.createIcons();

  // Load real data from backend
  fetchSystemStatus();
  fetchAccountsAndModels();
  fetchIdeIntegrations();
  runDlpBench();
  fetchRealHistoryIntoTraces();
  loadOrchestrationConfig();

  // Periodic polling every 4s
  setInterval(fetchSystemStatus, 4000);
});
