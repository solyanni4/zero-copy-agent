/**
 * zero-copy-agent Chrome Cookie Sync Service Worker (Manifest V3)
 * Provides background cookie replication to local zero-copy-agent without active chat tabs.
 */

const GEMINI_COOKIE_WHITELIST = new Set([
  "__Secure-1PSID",
  "__Secure-1PSIDTS",
  "__Secure-1PSIDCC",
  "__Secure-3PSID",
  "__Secure-3PSIDTS",
  "__Secure-3PSIDCC",
  "SID",
  "HSID",
  "SSID",
  "APISID",
  "SAPISID",
  "NID",
  "AEC",
  "OSID",
  "__Secure-OSID"
]);

const PROVIDERS = [
  {
    id: "gemini",
    name: "Gemini",
    url: "https://gemini.google.com/",
    domainFilter: "google.com",
    defaultAccountId: "gemini-web-primary",
    whitelist: GEMINI_COOKIE_WHITELIST
  },
  {
    id: "chatgpt",
    name: "ChatGPT",
    url: "https://chatgpt.com/",
    domainFilter: "chatgpt.com",
    defaultAccountId: "chatgpt-web-primary"
  },
  {
    id: "claude",
    name: "Claude",
    url: "https://claude.ai/",
    domainFilter: "claude.ai",
    defaultAccountId: "claude-web-primary"
  },
  {
    id: "deepseek",
    name: "DeepSeek",
    url: "https://chat.deepseek.com/",
    domainFilter: "deepseek.com",
    defaultAccountId: "deepseek-web-primary"
  },
  {
    id: "qwen",
    name: "Qwen",
    url: "https://chat.qwen.ai/",
    domainFilter: "qwen.ai",
    defaultAccountId: "qwen-web-primary"
  },
  {
    id: "grok",
    name: "Grok",
    url: "https://grok.com/",
    domainFilter: "grok.com",
    defaultAccountId: "grok-web-primary"
  }
];

let debounceTimer = null;

// Initialize defaults and register alarms
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

    // Schedule 30-minute recurring alarm
    chrome.alarms.create("syncCookiesAlarm", { periodInMinutes: 30 });

    // Initial sync
    await syncAllProviders();
  } catch (e) {
    // Silent fail on boot
  }
}

// Perform cookie extraction and synchronization
async function syncAllProviders() {
  const data = await chrome.storage.local.get(["zcaUrl", "syncToken", "accountIds", "syncStatuses"]);
  const zcaUrl = (data.zcaUrl || "http://127.0.0.1:8000").replace(/\/+$/, "");
  const syncToken = data.syncToken || "";
  const accountIds = data.accountIds || {};
  const statuses = data.syncStatuses || {};

  if (!syncToken) {
    return { ok: false, message: "Sync token missing" };
  }

  let totalSynced = 0;

  for (const prov of PROVIDERS) {
    const accountId = accountIds[prov.id] || prov.defaultAccountId;
    try {
      const allCookies = await chrome.cookies.getAll({ url: prov.url });
      let filtered = allCookies;

      if (prov.whitelist) {
        filtered = allCookies.filter(c => prov.whitelist.has(c.name));
      }

      if (!filtered || filtered.length === 0) {
        statuses[prov.id] = {
          status: "no_cookies",
          message: "Нет активных кук",
          cookieCount: 0,
          updatedAt: new Date().toISOString()
        };
        continue;
      }

      // Send to ZCA API
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
          cookies: filtered
        })
      });

      if (response.ok) {
        totalSynced++;
        statuses[prov.id] = {
          status: "ok",
          message: "Синхронизировано",
          cookieCount: filtered.length,
          updatedAt: new Date().toISOString()
        };
      } else {
        statuses[prov.id] = {
          status: "error",
          message: `Ошибка HTTP ${response.status}`,
          cookieCount: filtered.length,
          updatedAt: new Date().toISOString()
        };
      }
    } catch (netErr) {
      // Quietly log offline state without breaking UI or throwing errors
      statuses[prov.id] = {
        status: "offline",
        message: "ZCA сервер недоступен",
        cookieCount: 0,
        updatedAt: new Date().toISOString()
      };
    }
  }

  await chrome.storage.local.set({ syncStatuses: statuses, lastSyncTime: new Date().toISOString() });
  return { ok: true, syncedProviders: totalSynced };
}

// Lifecycle listeners
chrome.runtime.onInstalled.addListener(() => {
  initializeFromDefaults();
});

chrome.runtime.onStartup.addListener(() => {
  initializeFromDefaults();
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "syncCookiesAlarm") {
    syncAllProviders();
  }
});

// Watch cookie changes with 5s debounce for target AI domains
chrome.cookies.onChanged.addListener((changeInfo) => {
  const domain = (changeInfo.cookie && changeInfo.cookie.domain) || "";
  const isTarget = PROVIDERS.some(p => domain.includes(p.domainFilter));
  if (isTarget) {
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      syncAllProviders();
    }, 5000);
  }
});

// Internal message handler for popup interactions
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === "syncNow") {
    syncAllProviders().then(res => sendResponse(res));
    return true; // Keep message channel open for async response
  }
  if (request.action === "getStatus") {
    chrome.storage.local.get(["syncStatuses", "lastSyncTime", "zcaUrl", "syncToken", "accountIds"]).then(data => {
      sendResponse(data);
    });
    return true;
  }
});
