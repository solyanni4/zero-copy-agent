/**
 * zero-copy-agent Chrome Cookie Sync Service Worker (Manifest V3)
 * Provides background cookie replication to local zero-copy-agent without active chat tabs.
 */

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
  // Do not cut any cookies specifically targeting gemini.google.com
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
    url: "https://gemini.google.com/",
    domainFilter: "google.com",
    defaultAccountId: "gemini-web-primary"
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

let debounceTimer = null;
const pendingProviders = new Set();

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
    await syncAllProviders(false);
  } catch (e) {
    // Silent fail on boot
  }
}

// Perform cookie extraction and synchronization for a single provider
async function syncProvider(providerId, force = false, retryOn401 = true) {
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
      // Cookies did not change, skip sending network request
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
      } catch (err) {
        // Ignore fallback errors
      }
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

// Synchronizes all providers
async function syncAllProviders(force = false) {
  let count = 0;
  for (const prov of PROVIDERS) {
    const res = await syncProvider(prov.id, force);
    if (res.ok) count++;
  }
  return { ok: true, syncedProviders: count };
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
    syncAllProviders(false);
  }
});

// Watch cookie changes with 10s debounce targeting only the affected provider
chrome.cookies.onChanged.addListener((changeInfo) => {
  const domain = (changeInfo.cookie && changeInfo.cookie.domain) || "";
  const matched = PROVIDERS.filter(p => domain.includes(p.domainFilter));
  if (matched.length > 0) {
    for (const m of matched) {
      pendingProviders.add(m.id);
    }
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(async () => {
      const toSync = Array.from(pendingProviders);
      pendingProviders.clear();
      for (const pid of toSync) {
        await syncProvider(pid, false);
      }
    }, 10000); // 10s debounce
  }
});

// Internal message handler for popup interactions
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === "syncNow") {
    syncAllProviders(true).then(res => sendResponse(res));
    return true; // Keep message channel open for async response
  }
  if (request.action === "getStatus") {
    chrome.storage.local.get(["syncStatuses", "lastSyncTime", "zcaUrl", "syncToken", "accountIds"]).then(data => {
      sendResponse(data);
    });
    return true;
  }
});
