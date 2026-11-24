const PROVIDERS_META = [
  { id: "gemini", name: "Gemini", defaultAccountId: "gemini-web-primary" },
  { id: "chatgpt", name: "ChatGPT", defaultAccountId: "chatgpt-web-primary" },
  { id: "claude", name: "Claude", defaultAccountId: "claude-web-primary" },
  { id: "deepseek", name: "DeepSeek", defaultAccountId: "deepseek-web-primary" },
  { id: "qwen", name: "Qwen", defaultAccountId: "qwen-web-primary" },
  { id: "grok", name: "Grok", defaultAccountId: "grok-web-primary" }
];

document.addEventListener("DOMContentLoaded", async () => {
  const bridgeDot = document.getElementById("bridge-dot");
  const bridgeText = document.getElementById("bridge-text");
  const btnReconnect = document.getElementById("btn-reconnect");
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

      // Update bridge status
      if (data.wsConnected) {
        bridgeDot.className = "status-dot online";
        bridgeText.innerText = "Chrome Bridge подключен";
      } else {
        bridgeDot.className = "status-dot offline";
        bridgeText.innerText = "Мост отключен";
      }

      const accountIds = data.accountIds || {};
      const statuses = data.syncStatuses || {};
      const detectedProviders = data.detectedProviders || [];

      providersContainer.innerHTML = "";

      for (const prov of PROVIDERS_META) {
        const row = document.createElement("div");
        row.className = "provider-row";

        const currentAccountId = accountIds[prov.id] || prov.defaultAccountId;
        const isDetected = detectedProviders.includes(prov.id);
        const st = statuses[prov.id] || {};

        let badgeClass = "status-no_cookies";
        let badgeMsg = "Вход не выполнен";

        if (isDetected || st.status === "ok") {
          badgeClass = "status-ok";
          badgeMsg = "Сессия активна";
        } else if (st.status === "offline") {
          badgeClass = "status-offline";
          badgeMsg = "Офлайн";
        } else if (st.status === "error") {
          badgeClass = "status-error";
          badgeMsg = "Ошибка";
        }

        row.innerHTML = `
          <div class="provider-top">
            <span class="provider-name">${prov.name}</span>
            <div class="provider-actions">
              <span class="status-badge ${badgeClass}">${badgeMsg}</span>
              <button class="btn-xs btn-test" data-prov="${prov.id}">Тест</button>
            </div>
          </div>
          <input type="text" class="account-input" data-prov="${prov.id}" value="${currentAccountId}" placeholder="Account ID" />
        `;
        providersContainer.appendChild(row);
      }

      // Attach test listeners
      providersContainer.querySelectorAll(".btn-test").forEach(btn => {
        btn.addEventListener("click", async (e) => {
          const provId = e.target.getAttribute("data-prov");
          const origText = e.target.innerText;
          e.target.innerText = "...";
          e.target.disabled = true;

          const zcaUrl = (zcaUrlInput.value.trim() || "http://127.0.0.1:8000").replace(/\/+$/, "");
          try {
            const resp = await fetch(`${zcaUrl}/v1/bridge/test/${provId}`, {
              method: "POST"
            });
            const resJson = await resp.json();
            if (resp.ok && resJson.status === "success") {
              e.target.innerText = "OK!";
            } else {
              e.target.innerText = "Сбой";
              alert(`Тест ${provId} не удался: ${resJson.error || JSON.stringify(resJson)}`);
            }
          } catch (netErr) {
            e.target.innerText = "Сбой";
            alert(`Ошибка сети при тесте ${provId}: ${netErr.message}`);
          }

          setTimeout(() => {
            e.target.innerText = origText;
            e.target.disabled = false;
          }, 3000);
        });
      });

      if (data.lastSyncTime) {
        const d = new Date(data.lastSyncTime);
        footerNote.innerText = `Куки синхронизированы: ${d.toLocaleTimeString()}`;
      }
    });
  }

  await loadData();

  btnReconnect.addEventListener("click", () => {
    btnReconnect.innerText = "...";
    chrome.runtime.sendMessage({ action: "reconnectWs" }, () => {
      setTimeout(() => {
        btnReconnect.innerText = "Переподключить";
        loadData();
      }, 500);
    });
  });

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
    chrome.runtime.sendMessage({ action: "reconnectWs" }, () => {});
    setTimeout(() => { btnSave.innerText = "Сохранить"; }, 1500);
  });

  btnSync.addEventListener("click", () => {
    btnSync.innerText = "Синхронизация...";
    btnSync.disabled = true;

    chrome.runtime.sendMessage({ action: "syncNow" }, async () => {
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
      btnSync.innerText = "Синхронизировать куки";
      loadData();
    });
  });
});
