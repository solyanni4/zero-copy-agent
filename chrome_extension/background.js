/**
 * zero-copy-agent Chrome Bridge & Cookie Sync Service Worker (Manifest V3)
 * Provides:
 * 1. Low-latency WebSocket Bridge to execute prompts directly in active browser tabs.
 * 2. Background cookie replication as fallback for Playwright sessions.
 */
// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Solyanni4 (https://github.com/solyanni4/zero-copy-agent)

const GOOGLE_ALLOWED_EXACT = new Set([
  "SID",
  "HSID",
  "SSID",
  "APISID",
  "SAPISID",
  "SIDCC",
  "OSID",
  "NID",
  "AEC",
  "SOCS",
  "CONSENT",
  "1P_JAR",
  "SEARCH_SAMESITE"
]);

function isGeminiCookieAllowed(cookie) {
  const domain = cookie.domain || "";
  if (domain.includes("gemini.google.com")) return true;
  const name = cookie.name || "";
  if (GOOGLE_ALLOWED_EXACT.has(name)) return true;
  if (
    name.startsWith("__Secure-1P") ||
    name.startsWith("__Secure-3P") ||
    name.startsWith("__Secure-OSID") ||
    name.startsWith("__Host-")
  ) {
    return true;
  }
  return false;
}

const PROVIDERS = [
  {
    id: "gemini",
    name: "Gemini",
    url: "https://gemini.google.com/app",
    matchUrl: "https://gemini.google.com/",
    domainFilter: "google.com",
    defaultAccountId: "gemini-web-primary"
  },
  {
    id: "chatgpt",
    name: "ChatGPT",
    url: "https://chatgpt.com/",
    matchUrl: "https://chatgpt.com/",
    domainFilter: "chatgpt.com",
    defaultAccountId: "chatgpt-web-primary"
  },
  {
    id: "claude",
    name: "Claude",
    url: "https://claude.ai/new",
    matchUrl: "https://claude.ai/",
    domainFilter: "claude.ai",
    defaultAccountId: "claude-web-primary"
  },
  {
    id: "deepseek",
    name: "DeepSeek",
    url: "https://chat.deepseek.com/",
    matchUrl: "https://chat.deepseek.com/",
    domainFilter: "deepseek.com",
    defaultAccountId: "deepseek-web-primary"
  },
  {
    id: "qwen",
    name: "Qwen",
    url: "https://chat.qwen.ai/",
    matchUrl: "https://chat.qwen.ai/",
    domainFilter: "qwen.ai",
    defaultAccountId: "qwen-web-primary"
  },
  {
    id: "grok",
    name: "Grok",
    url: "https://grok.com/",
    matchUrl: "https://grok.com/",
    domainFilter: "grok.com",
    defaultAccountId: "grok-web-primary"
  }
];

const KEY_AUTH_COOKIES = {
  gemini: ["__Secure-1PSID", "SID"],
  chatgpt: ["__Secure-next-auth.session-token", "__Secure-next-auth.session-token.0"],
  claude: ["sessionKey"],
  deepseek: ["ds_session_id", "userToken"],
  qwen: ["token", "login_aliyunid_ticket", "cna"],
  grok: ["sso", "sso-rw"]
};

function hasKeyAuthCookies(providerId, cookies) {
  const keys = KEY_AUTH_COOKIES[providerId];
  if (!keys || keys.length === 0) return true;
  return (cookies || []).some(c => keys.includes(c.name));
}

function computeCookieSignature(cookies) {
  if (!cookies || !cookies.length) return "";
  return cookies
    .map(c => `${c.domain || ""}|${c.name || ""}|${c.value || ""}`)
    .sort()
    .join(";;");
}

let ws = null;
let wsConnected = false;
let wsConnecting = false;
let reconnectTimer = null;
let reconnectDelay = 2000;
let lastReportedProvidersJson = "";
let pingInterval = null;

const providerQueues = {};
const activeTaskPorts = {};

async function getDetectedActiveProviders() {
  const detected = [];
  for (const prov of PROVIDERS) {
    try {
      const cookies = await chrome.cookies.getAll({ domain: prov.domainFilter });
      if (hasKeyAuthCookies(prov.id, cookies)) {
        detected.push(prov.id);
      }
    } catch (e) {}
  }
  return detected;
}

function getWsUrl(zcaUrl, token) {
  let wsBase = zcaUrl.replace(/^http:\/\//, "ws://").replace(/^https:\/\//, "wss://");
  wsBase = wsBase.replace(/\/+$/, "");
  return `${wsBase}/ws/bridge?token=${encodeURIComponent(token)}`;
}

function startPingLoop() {
  if (pingInterval) clearInterval(pingInterval);
  pingInterval = setInterval(() => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      sendWs({ type: "ping", ts: Date.now() });
    }
  }, 20000);
}

function stopPingLoop() {
  if (pingInterval) {
    clearInterval(pingInterval);
    pingInterval = null;
  }
}

async function connectWebSocket() {
  if (wsConnected || (ws && ws.readyState === WebSocket.OPEN) || wsConnecting) return;
  wsConnecting = true;

  try {
    const data = await chrome.storage.local.get(["zcaUrl", "syncToken"]);
    let zcaUrl = data.zcaUrl || "http://127.0.0.1:8000";
    let token = data.syncToken || "";

    if (!token) {
      try {
        const defResp = await fetch(chrome.runtime.getURL("defaults.json"));
        if (defResp.ok) {
          const defaults = await defResp.json();
          if (defaults.syncToken) {
            token = defaults.syncToken;
            await chrome.storage.local.set({ syncToken: token });
          }
        }
      } catch (e) {}
    }

    if (!token) {
      console.warn("[ZCA Bridge] Missing syncToken; aborting WebSocket connection");
      wsConnecting = false;
      scheduleReconnect();
      return;
    }

    const wsUrl = getWsUrl(zcaUrl, token);
    console.log("[ZCA Bridge] Connecting to WebSocket:", wsUrl);

    ws = new WebSocket(wsUrl);

    ws.onopen = async () => {
      console.log("[ZCA Bridge] WebSocket connected successfully");
      wsConnected = true;
      wsConnecting = false;
      reconnectDelay = 2000;
      startPingLoop();
      await chrome.storage.local.set({ bridgeConnected: true, bridgeLastSeen: Date.now() });

      const activeProviders = await getDetectedActiveProviders();
      activeProviders.sort();
      lastReportedProvidersJson = JSON.stringify(activeProviders);

      const helloMsg = {
        type: "hello",
        version: "1.6.0",
        providers: activeProviders,
        user_agent: navigator.userAgent
      };
      ws.send(JSON.stringify(helloMsg));
    };

    ws.onmessage = async (evt) => {
      await chrome.storage.local.set({ bridgeLastSeen: Date.now() });
      let msg = null;
      try {
        msg = JSON.parse(evt.data);
      } catch (err) {
        return;
      }

      if (msg.type === "ping") {
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "pong", ts: Date.now() }));
        }
      } else if (msg.type === "pong") {
        await chrome.storage.local.set({ bridgeLastSeen: Date.now() });
      } else if (msg.type === "prompt") {
        // Immediately acknowledge receipt before any async operations or tab creation
        try {
          if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({
              type: "status",
              task_id: msg.task_id,
              stage: "received",
              detail: `Processing ${msg.provider || "prompt"} in Chrome Bridge`
            }));
          }
        } catch (ackErr) {
          console.warn("[ZCA Bridge] Failed to send instant 'received' ack:", ackErr);
        }
        enqueuePromptTask(msg);
      } else if (msg.type === "cancel") {
        cancelPromptTask(msg.task_id);
      }
    };

    ws.onclose = async (evt) => {
      console.warn(`[ZCA Bridge] WebSocket disconnected (code=${evt.code}, reason=${evt.reason})`);
      stopPingLoop();
      wsConnected = false;
      wsConnecting = false;
      ws = null;
      await chrome.storage.local.set({ bridgeConnected: false });

      if (evt.code === 4401) {
        try {
          const defResp = await fetch(chrome.runtime.getURL("defaults.json"));
          if (defResp.ok) {
            const defaults = await defResp.json();
            if (defaults.syncToken) {
              await chrome.storage.local.set({ syncToken: defaults.syncToken });
            }
          }
        } catch (e) {}
      }

      scheduleReconnect();
    };

    ws.onerror = (err) => {
      console.warn("[ZCA Bridge] WebSocket error:", err);
      try {
        ws.close();
      } catch (e) {}
    };
  } catch (err) {
    wsConnecting = false;
    scheduleReconnect();
  }
}

function scheduleReconnect() {
  if (reconnectTimer) clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(() => {
    if (!wsConnected && (!ws || ws.readyState !== WebSocket.OPEN)) {
      connectWebSocket();
    }
  }, reconnectDelay);
  reconnectDelay = Math.min(reconnectDelay * 2, 10000);
}

function sendWs(msg) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    try {
      ws.send(JSON.stringify(msg));
      return true;
    } catch (e) {
      return false;
    }
  }
  return false;
}

function enqueuePromptTask(taskMsg) {
  const provId = (taskMsg.provider || "").toLowerCase();
  if (!providerQueues[provId]) {
    providerQueues[provId] = [];
  }
  providerQueues[provId].push(taskMsg);
  processProviderQueue(provId);
}

let isProcessingQueue = {};

async function processProviderQueue(provId) {
  if (isProcessingQueue[provId]) return;
  const queue = providerQueues[provId];
  if (!queue || queue.length === 0) return;

  isProcessingQueue[provId] = true;
  const task = queue.shift();

  try {
    await executePromptInTab(task);
  } catch (err) {
    sendWs({
      type: "error",
      task_id: task.task_id,
      code: "internal_error",
      message: err.message || String(err)
    });
  } finally {
    isProcessingQueue[provId] = false;
    if (queue && queue.length > 0) {
      processProviderQueue(provId);
    }
  }
}

function cancelPromptTask(taskId) {
  const port = activeTaskPorts[taskId];
  if (port) {
    try {
      port.postMessage({ type: "cancel", task_id: taskId });
    } catch (e) {}
    delete activeTaskPorts[taskId];
  }
}

async function findOrCreateProviderTab(providerDef) {
  const tabs = await chrome.tabs.query({});
  for (const t of tabs) {
    if (t.url && t.url.startsWith(providerDef.matchUrl)) {
      return t;
    }
  }

  const newTab = await chrome.tabs.create({
    url: providerDef.url,
    active: false
  });

  await new Promise((resolve) => {
    function listener(tabId, info) {
      if (tabId === newTab.id && info.status === "complete") {
        chrome.tabs.onUpdated.removeListener(listener);
        resolve(newTab);
      }
    }
    chrome.tabs.onUpdated.addListener(listener);
    setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(listener);
      resolve(newTab);
    }, 15000);
  });

  return newTab;
}

async function ensureTabLoadedAndReady(tab) {
  let currentTab = tab;
  if (currentTab.status !== "complete") {
    await new Promise((resolve) => {
      const timeout = setTimeout(() => {
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
      }, 15000);
      function listener(tabId, info) {
        if (tabId === currentTab.id && info.status === "complete") {
          clearTimeout(timeout);
          chrome.tabs.onUpdated.removeListener(listener);
          resolve();
        }
      }
      chrome.tabs.onUpdated.addListener(listener);
    });
  }

  // Extra 1000ms delay for SPA DOM hydration
  await new Promise(r => setTimeout(r, 1000));

  // Ping content script to confirm readiness
  let contentReady = false;
  try {
    const pingResp = await new Promise((resolve) => {
      chrome.tabs.sendMessage(currentTab.id, { action: "ping_content" }, (resp) => {
        if (chrome.runtime.lastError || !resp || !resp.ok) {
          resolve(null);
        } else {
          resolve(resp);
        }
      });
    });
    if (pingResp && pingResp.ok) {
      contentReady = true;
    }
  } catch (e) {}

  if (!contentReady) {
    try {
      await chrome.scripting.executeScript({
        target: { tabId: currentTab.id },
        files: ["content_bridge.js"]
      });
      await new Promise(r => setTimeout(r, 300));
    } catch (injErr) {
      console.warn("[ZCA Bridge] Script injection warning:", injErr);
    }
  }
}

async function executePromptInTab(task) {
  const provId = (task.provider || "").toLowerCase();
  const provDef = PROVIDERS.find(p => p.id === provId);

  if (!provDef) {
    sendWs({
      type: "error",
      task_id: task.task_id,
      code: "unknown_provider",
      message: `Unknown provider: ${provId}`
    });
    return;
  }

  sendWs({
    type: "status",
    task_id: task.task_id,
    stage: "received",
    detail: `Processing ${provDef.name} prompt`
  });

  let tab = null;
  try {
    tab = await findOrCreateProviderTab(provDef);
    if (!tab || !tab.id) {
      throw new Error(`Failed to open or locate tab for ${provDef.name}`);
    }

    sendWs({
      type: "status",
      task_id: task.task_id,
      stage: "tab_ready",
      detail: `Tab ${tab.id} active`
    });

    await ensureTabLoadedAndReady(tab);

    sendWs({
      type: "status",
      task_id: task.task_id,
      stage: "injected",
      detail: "Content bridge script confirmed ready"
    });
  } catch (setupErr) {
    sendWs({
      type: "error",
      task_id: task.task_id,
      code: "tab_setup_error",
      message: setupErr.message || String(setupErr)
    });
    return;
  }

  return new Promise((resolve) => {
    let port = null;
    let taskFinished = false;
    try {
      port = chrome.tabs.connect(tab.id, { name: "zca-bridge" });
    } catch (err) {
      sendWs({
        type: "error",
        task_id: task.task_id,
        code: "connection_error",
        message: `Failed to connect to page script: ${err.message}`
      });
      resolve();
      return;
    }

    activeTaskPorts[task.task_id] = port;

    port.onMessage.addListener((msg) => {
      if (msg.type === "status") {
        sendWs({
          type: "status",
          task_id: task.task_id,
          stage: msg.stage,
          detail: msg.detail
        });
      } else if (msg.type === "chunk") {
        sendWs({
          type: "chunk",
          task_id: task.task_id,
          delta: msg.delta,
          full_text: msg.full_text
        });
      } else if (msg.type === "done") {
        taskFinished = true;
        sendWs({
          type: "done",
          task_id: task.task_id,
          full_text: msg.full_text
        });
        delete activeTaskPorts[task.task_id];
        try { port.disconnect(); } catch (e) {}
        resolve();
      } else if (msg.type === "error") {
        taskFinished = true;
        sendWs({
          type: "error",
          task_id: task.task_id,
          code: msg.code,
          message: msg.message
        });
        delete activeTaskPorts[task.task_id];
        try { port.disconnect(); } catch (e) {}
        resolve();
      }
    });

    port.onDisconnect.addListener(() => {
      delete activeTaskPorts[task.task_id];
      if (!taskFinished) {
        sendWs({
          type: "error",
          task_id: task.task_id,
          code: "port_disconnected",
          message: "Content script port disconnected prematurely"
        });
      }
      resolve();
    });

    port.postMessage({
      type: "execute_task",
      task_id: task.task_id,
      prompt: task.prompt,
      model: task.model,
      timeout_sec: task.timeout_sec || 120
    });
  });
}

// Background Cookie Replication (Fallback when bridge is inactive)
let debounceTimer = null;
const pendingProviders = new Set();

async function initializeFromDefaults() {
  try {
    const url = chrome.runtime.getURL("defaults.json");
    const resp = await fetch(url);
    if (!resp.ok) return;
    const defaults = await resp.json();

    const stored = await chrome.storage.local.get(["zcaUrl", "syncToken", "accountIds"]);
    const updates = {};

    if (!stored.zcaUrl && defaults.zcaUrl) {
      updates.zcaUrl = defaults.zcaUrl;
    }
    if (defaults.syncToken && (!stored.syncToken || stored.syncToken !== defaults.syncToken)) {
      updates.syncToken = defaults.syncToken;
    }
    if (!stored.accountIds) {
      updates.accountIds = {
        gemini: "gemini-web-primary",
        chatgpt: "chatgpt-web-primary",
        claude: "claude-web-primary",
        deepseek: "deepseek-web-primary",
        qwen: "qwen-web-primary",
        grok: "grok-web-primary"
      };
    }

    if (Object.keys(updates).length > 0) {
      await chrome.storage.local.set(updates);
    }

    chrome.alarms.create("syncCookiesAlarm", { periodInMinutes: 30 });
    chrome.alarms.create("bridgeKeepAlive", { periodInMinutes: 1 });

    await connectWebSocket();
  } catch (e) {}
}

async function syncProvider(providerId, force = false, retryOn401 = true) {
  // Only sync via HTTP when explicitly requested by user in popup UI
  if (!force) {
    return { ok: true, status: "auto_sync_disabled" };
  }
  // If Chrome Bridge is actively connected, skip HTTP cookie pushing
  if (wsConnected && ws && ws.readyState === WebSocket.OPEN) {
    return { ok: true, status: "bridge_active" };
  }

  const prov = PROVIDERS.find(p => p.id === providerId);
  if (!prov) return { ok: false, message: "Unknown provider" };

  const data = await chrome.storage.local.get([
    "zcaUrl", "syncToken", "accountIds", "syncStatuses", "cookieSignatures"
  ]);
  const zcaUrl = (data.zcaUrl || "http://127.0.0.1:8000").replace(/\/+$/, "");
  const syncToken = data.syncToken || "";
  const accountIds = data.accountIds || {};
  const statuses = data.syncStatuses || {};
  const signatures = data.cookieSignatures || {};

  if (!syncToken) {
    return { ok: false, message: "Sync token missing" };
  }

  const accountId = accountIds[prov.id] || prov.defaultAccountId;

  try {
    const allCookies = await chrome.cookies.getAll({ url: prov.url });
    let filtered = allCookies;
    if (prov.id === "gemini") {
      filtered = allCookies.filter(isGeminiCookieAllowed);
    }

    if (!filtered || filtered.length === 0 || !hasKeyAuthCookies(prov.id, filtered)) {
      statuses[prov.id] = {
        status: "no_cookies",
        message: "Не выполнен вход в Chrome",
        cookieCount: filtered ? filtered.length : 0,
        updatedAt: new Date().toISOString()
      };
      await chrome.storage.local.set({ syncStatuses: statuses });
      return { ok: true, status: "no_cookies" };
    }

    const currentSig = computeCookieSignature(filtered);
    if (!force && signatures[prov.id] === currentSig) {
      return { ok: true, status: "unchanged", cookieCount: filtered.length };
    }

    const userAgent = typeof navigator !== "undefined" && navigator.userAgent ? navigator.userAgent : "";
    const targetEndpoint = `${zcaUrl}/v1/auth/sync-cookies`;
    const response = await fetch(targetEndpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-ZCA-Sync-Token": syncToken
      },
      body: JSON.stringify({
        provider: prov.id,
        account_id: accountId,
        user_agent: userAgent,
        cookies: filtered
      })
    });

    if (response.status === 401 && retryOn401) {
      try {
        const defUrl = chrome.runtime.getURL("defaults.json");
        const defResp = await fetch(defUrl);
        if (defResp.ok) {
          const defaults = await defResp.json();
          if (defaults.syncToken && defaults.syncToken !== syncToken) {
            await chrome.storage.local.set({ syncToken: defaults.syncToken });
            return await syncProvider(providerId, force, false);
          }
        }
      } catch (err) {}
    }

    const resJson = await response.json().catch(() => ({}));

    if (response.ok) {
      signatures[prov.id] = currentSig;
      statuses[prov.id] = {
        status: "ok",
        message: resJson.status === "unchanged" ? "Актуально (без изменений)" : "Синхронизировано",
        cookieCount: filtered.length,
        updatedAt: new Date().toISOString()
      };
      await chrome.storage.local.set({
        syncStatuses: statuses,
        cookieSignatures: signatures,
        lastSyncTime: new Date().toISOString()
      });
      return { ok: true, status: resJson.status || "ok", cookieCount: filtered.length };
    } else {
      statuses[prov.id] = {
        status: "error",
        message: `Ошибка HTTP ${response.status}`,
        cookieCount: filtered.length,
        updatedAt: new Date().toISOString()
      };
      await chrome.storage.local.set({ syncStatuses: statuses });
      return { ok: false, error: response.status };
    }
  } catch (netErr) {
    statuses[prov.id] = {
      status: "offline",
      message: "ZCA сервер недоступен",
      cookieCount: 0,
      updatedAt: new Date().toISOString()
    };
    await chrome.storage.local.set({ syncStatuses: statuses });
    return { ok: false, error: netErr.message };
  }
}

async function syncAllProviders(force = false) {
  if (wsConnected && ws && ws.readyState === WebSocket.OPEN) {
    return { ok: true, syncedProviders: 0, message: "Chrome Bridge active" };
  }
  let count = 0;
  for (const prov of PROVIDERS) {
    const res = await syncProvider(prov.id, force);
    if (res.ok) count++;
  }
  return { ok: true, syncedProviders: count };
}

chrome.runtime.onInstalled.addListener(() => {
  initializeFromDefaults();
});

chrome.runtime.onStartup.addListener(() => {
  initializeFromDefaults();
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "syncCookiesAlarm") {
    // Legacy HTTP cookie sync disabled; Chrome Bridge WebSocket handles real-time execution
  } else if (alarm.name === "bridgeKeepAlive") {
    if (!wsConnected && (!ws || ws.readyState !== WebSocket.OPEN)) {
      connectWebSocket();
    }
  }
});

chrome.cookies.onChanged.addListener((changeInfo) => {
  const domain = (changeInfo.cookie && changeInfo.cookie.domain) || "";
  const matched = PROVIDERS.filter(p => domain.includes(p.domainFilter));
  if (matched.length > 0) {
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(async () => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        const activeProviders = await getDetectedActiveProviders();
        activeProviders.sort();
        const curJson = JSON.stringify(activeProviders);
        if (curJson !== lastReportedProvidersJson) {
          lastReportedProvidersJson = curJson;
          sendWs({
            type: "hello",
            version: "1.6.0",
            providers: activeProviders,
            user_agent: navigator.userAgent
          });
        }
      }
    }, 10000);
  }
});

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === "syncNow") {
    syncAllProviders(true).then(res => sendResponse(res));
    return true;
  }
  if (request.action === "reconnectWs") {
    connectWebSocket().then(() => sendResponse({ ok: true, connected: wsConnected }));
    return true;
  }
  if (request.action === "getStatus") {
    chrome.storage.local.get([
      "syncStatuses", "lastSyncTime", "zcaUrl", "syncToken", "accountIds", "bridgeConnected", "bridgeLastSeen"
    ]).then(async (data) => {
      data.wsConnected = wsConnected;
      data.detectedProviders = await getDetectedActiveProviders();
      sendResponse(data);
    });
    return true;
  }
});
