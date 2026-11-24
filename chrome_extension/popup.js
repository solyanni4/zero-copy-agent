const PROVIDERS_META = [
  { id: "gemini", name: "Gemini", defaultAccountId: "gemini-web-primary" },
  { id: "chatgpt", name: "ChatGPT", defaultAccountId: "chatgpt-web-primary" },
  { id: "claude", name: "Claude", defaultAccountId: "claude-web-primary" },
  { id: "deepseek", name: "DeepSeek", defaultAccountId: "deepseek-web-primary" },
  { id: "qwen", name: "Qwen", defaultAccountId: "qwen-web-primary" },
  { id: "grok", name: "Grok", defaultAccountId: "grok-web-primary" }
];

document.addEventListener("DOMContentLoaded", async () => {
  const zcaUrlInput = document.getElementById("zca-url");
  const syncTokenInput = document.getElementById("sync-token");
  const providersContainer = document.getElementById("providers-container");
  const btnSync = document.getElementById("btn-sync");
  const btnSave = document.getElementById("btn-save");
  const footerNote = document.getElementById("sync-footer");

  async function loadData() {
    chrome.runtime.sendMessage({ action: "getStatus" }, (data) => {
      if (!data) return;
      zcaUrlInput.value = data.zcaUrl || "http://127.0.0.1:8000";
      syncTokenInput.value = data.syncToken || "";

      const accountIds = data.accountIds || {};
      const statuses = data.syncStatuses || {};

      providersContainer.innerHTML = "";

      for (const prov of PROVIDERS_META) {
        const row = document.createElement("div");
        row.className = "provider-row";

        const currentAccountId = accountIds[prov.id] || prov.defaultAccountId;
        const st = statuses[prov.id] || { status: "no_cookies", message: "Не синхронизировано" };

        let badgeClass = "status-no_cookies";
        if (st.status === "ok") badgeClass = "status-ok";
        else if (st.status === "offline") badgeClass = "status-offline";
        else if (st.status === "error") badgeClass = "status-error";

        const countText = st.cookieCount ? ` (${st.cookieCount} кук)` : "";

        row.innerHTML = `
          <div class="provider-top">
            <span class="provider-name">${prov.name}</span>
            <span class="status-badge ${badgeClass}">${st.message}${countText}</span>
          </div>
          <input type="text" class="account-input" data-prov="${prov.id}" value="${currentAccountId}" placeholder="Account ID" />
        `;
        providersContainer.appendChild(row);
      }

      if (data.lastSyncTime) {
        const d = new Date(data.lastSyncTime);
        footerNote.innerText = `Посл. синк: ${d.toLocaleTimeString()}`;
      }
    });
  }

  await loadData();

  btnSave.addEventListener("click", async () => {
    const accountIds = {};
    const inputs = providersContainer.querySelectorAll(".account-input");
    inputs.forEach(inp => {
      const p = inp.getAttribute("data-prov");
      accountIds[p] = inp.value.trim() || `${p}-web-primary`;
    });

    await chrome.storage.local.set({
      zcaUrl: zcaUrlInput.value.trim(),
      syncToken: syncTokenInput.value.trim(),
      accountIds: accountIds
    });

    btnSave.innerText = "Сохранено!";
    setTimeout(() => { btnSave.innerText = "Сохранить"; }, 1500);
  });

  btnSync.addEventListener("click", () => {
    btnSync.innerText = "Синхронизация...";
    btnSync.disabled = true;

    chrome.runtime.sendMessage({ action: "syncNow" }, async (res) => {
      try {
        const defUrl = chrome.runtime.getURL("defaults.json");
        const defResp = await fetch(defUrl);
        if (defResp.ok) {
          const defaults = await defResp.json();
          const stored = await chrome.storage.local.get(["syncToken"]);
          if (defaults.syncToken && defaults.syncToken !== stored.syncToken) {
            await chrome.storage.local.set({ syncToken: defaults.syncToken });
            await new Promise(r => chrome.runtime.sendMessage({ action: "syncNow" }, r));
          }
        }
      } catch (e) {}

      btnSync.disabled = false;
      btnSync.innerText = "Синхронизировать сейчас";
      loadData();
    });
  });
});
