/**
 * zero-copy-agent Chrome Bridge Content Script
 * Executes prompts and streams responses directly in active browser tabs
 * for Gemini, ChatGPT, Claude, DeepSeek, Qwen, and Grok.
 */
// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Solyanni4 (https://github.com/solyanni4/zero-copy-agent)

(function () {
  if (window.__ZCA_CONTENT_BRIDGE_LOADED__) {
    return;
  }
  window.__ZCA_CONTENT_BRIDGE_LOADED__ = true;

  console.log("[ZCA ContentBridge] Initialized on", window.location.href);

  function detectProvider() {
    const host = window.location.hostname;
    if (host.includes("gemini.google.com")) return "gemini";
    if (host.includes("chatgpt.com")) return "chatgpt";
    if (host.includes("claude.ai")) return "claude";
    if (host.includes("deepseek.com")) return "deepseek";
    if (host.includes("qwen.ai")) return "qwen";
    if (host.includes("grok.com")) return "grok";
    return "unknown";
  }

  const PROVIDER = detectProvider();

  function isCloudflareChallenge() {
    const title = (document.title || "").toLowerCase();
    if (title.includes("just a moment") || title.includes("attention required") || title.includes("cloudflare")) {
      return true;
    }
    const cfElem = document.querySelector("#challenge-running, #cf-challenge-running, .cf-turnstile-wrapper, #turnstile-wrapper");
    return Boolean(cfElem);
  }

  function isNeedsAuth() {
    const bodyText = (document.body ? document.body.innerText : "").toLowerCase();
    const url = window.location.href.toLowerCase();

    if (url.includes("servicelogin") || url.includes("interactivelogin") || url.includes("/login") || url.includes("/auth")) {
      return true;
    }

    if (PROVIDER === "gemini") {
      const signin = document.querySelector('a[href*="ServiceLogin"], a[href*="InteractiveLogin"]');
      if (signin) return true;
    } else if (PROVIDER === "chatgpt") {
      const loginBtn = document.querySelector('button[data-testid="login-button"], a[href*="/login"], a[href*="/signup"]');
      if (loginBtn) return true;
    } else if (PROVIDER === "claude") {
      const loginBtn = document.querySelector('a[href*="/login"], input[type="email"]');
      if (loginBtn) return true;
    } else if (PROVIDER === "deepseek") {
      if (bodyText.includes("войти в аккаунт") || bodyText.includes("log in to deepseek") || bodyText.includes("sign in")) {
        return true;
      }
    } else if (PROVIDER === "qwen") {
      if (url.includes("login") || bodyText.includes("please sign in")) {
        return true;
      }
    } else if (PROVIDER === "grok") {
      const loginBtn = document.querySelector('a[href*="/login"], button[data-testid*="login"]');
      if (loginBtn) return true;
    }

    return false;
  }

  function getInputSelector() {
    switch (PROVIDER) {
      case "gemini":
        return 'div.ql-editor[contenteditable="true"], rich-textarea [contenteditable="true"], [role="textbox"][contenteditable="true"], rich-textarea div[contenteditable="true"], textarea';
      case "chatgpt":
        return '#prompt-textarea, div#prompt-textarea[contenteditable="true"], textarea[data-id="root"], [contenteditable="true"], textarea';
      case "claude":
        return 'div.ProseMirror[contenteditable="true"], fieldset div[contenteditable="true"], div[contenteditable="true"], textarea';
      case "deepseek":
        return '#chat-input, textarea[placeholder*="DeepSeek" i], textarea[placeholder*="Сообщение" i], textarea, div[contenteditable="true"]';
      case "qwen":
        return '#chat-input, textarea[placeholder*="Qwen" i], textarea[placeholder*="Ask" i], textarea, div[contenteditable="true"]';
      case "grok":
        return 'textarea[placeholder*="Grok" i], textarea[placeholder*="Ask" i], textarea, div[contenteditable="true"]';
      default:
        return 'textarea, [contenteditable="true"]';
    }
  }

  function getSendButton() {
    switch (PROVIDER) {
      case "gemini": {
        const matSend = document.querySelector('mat-icon[fonticon="send"], mat-icon[data-mat-icon-name="send"]');
        const matSendBtn = matSend ? matSend.closest("button") : null;
        if (matSendBtn && !matSendBtn.disabled && matSendBtn.getAttribute("aria-disabled") !== "true") {
          return matSendBtn;
        }
        return document.querySelector(
          'button.send-button:not([disabled]), ' +
          'button[aria-label*="Send" i]:not([disabled]), ' +
          'button[aria-label*="Отправить" i]:not([disabled]), ' +
          'button[data-test-id="send-button"]:not([disabled]), ' +
          'button[aria-label*="Submit" i]:not([disabled]), ' +
          'button[data-testid*="send" i]:not([disabled])'
        );
      }
      case "chatgpt":
        return document.querySelector(
          'button[data-testid="send-button"]:not([disabled]), ' +
          'button[aria-label*="Send" i]:not([disabled]), ' +
          'button[aria-label*="Отправить" i]:not([disabled]), ' +
          'button[data-testid*="send"]:not([disabled])'
        );
      case "claude":
        return document.querySelector(
          'button[aria-label*="Send" i]:not([disabled]), ' +
          'button[aria-label*="Отправить" i]:not([disabled]), ' +
          'button[aria-label*="Submit" i]:not([disabled])'
        );
      case "deepseek":
        return document.querySelector(
          'button[aria-label*="Send" i]:not([disabled]), ' +
          'button[aria-label*="Отправить" i]:not([disabled]), ' +
          'div[role="button"][aria-label*="Send" i], ' +
          '.ds-icon-button:not([disabled]), ' +
          'button[class*="send" i]:not([disabled])'
        );
      case "qwen":
        return document.querySelector(
          'button[class*="send" i]:not([disabled]), ' +
          'button[aria-label*="Send" i]:not([disabled]), ' +
          'button[aria-label*="Отправить" i]:not([disabled]), ' +
          '.ant-btn-primary:not([disabled])'
        );
      case "grok":
        return document.querySelector(
          'button[aria-label*="Submit" i]:not([disabled]), ' +
          'button[aria-label*="Grok" i]:not([disabled]), ' +
          'button[type="submit"]:not([disabled])'
        );
      default:
        return document.querySelector('button[type="submit"]:not([disabled]), button[aria-label*="Send" i]:not([disabled])');
    }
  }

  function getStopButton() {
    switch (PROVIDER) {
      case "gemini": {
        const matStop = document.querySelector('mat-icon[fonticon="stop"], mat-icon[data-mat-icon-name="stop"]');
        const matStopBtn = matStop ? matStop.closest("button") : null;
        if (matStopBtn && matStopBtn.offsetParent !== null) {
          return matStopBtn;
        }
        return document.querySelector('button[aria-label*="Stop" i], button[aria-label*="Остановить" i], button[data-testid*="stop" i], .stop-icon, button.stop-button');
      }
      case "chatgpt":
        return document.querySelector('button[data-testid="stop-button"], button[aria-label*="Stop" i], button[aria-label*="Остановить" i]');
      case "claude":
        return document.querySelector('button[aria-label*="Stop" i], button[aria-label*="Остановить" i]');
      case "deepseek":
        return document.querySelector('button[class*="stop" i], button[aria-label*="Stop" i], button[aria-label*="Остановить" i], .ds-icon-button[title*="Stop" i]');
      case "qwen":
        return document.querySelector('button[class*="stop" i], button[aria-label*="Stop" i], button[aria-label*="Остановить" i]');
      case "grok":
        return document.querySelector('button[aria-label*="Stop" i], button[aria-label*="Остановить" i]');
      default:
        return document.querySelector('button[aria-label*="Stop" i], button[aria-label*="Остановить" i]');
    }
  }

  function isGenerating() {
    const stopBtn = getStopButton();
    if (stopBtn && (stopBtn.offsetParent !== null || stopBtn.getClientRects().length > 0)) {
      return true;
    }

    switch (PROVIDER) {
      case "gemini": {
        const activeThinking = document.querySelector(
          'model-thoughts[is-thinking="true"], ' +
          'model-thoughts mat-progress-bar, ' +
          'mat-progress-bar, ' +
          'mat-spinner, ' +
          '[aria-busy="true"], ' +
          '.sparkle-animation, ' +
          '.loading, ' +
          '[aria-label*="Loading" i]'
        );
        if (activeThinking) return true;

        const sendBtn = getSendButton();
        if (sendBtn && !sendBtn.disabled && (sendBtn.offsetParent !== null || sendBtn.getClientRects().length > 0)) {
          return false;
        }
        return false;
      }
      case "claude": {
        const streaming = document.querySelector('div[data-is-streaming="true"]');
        return Boolean(streaming);
      }
      case "chatgpt": {
        const busy = document.querySelector('.result-streaming, [data-is-streaming="true"]');
        return Boolean(busy);
      }
      case "deepseek": {
        const busy = document.querySelector('.ds-loading, [aria-busy="true"]');
        return Boolean(busy);
      }
      default:
        return Boolean(document.querySelector('[aria-busy="true"], .result-streaming, .loading'));
    }
  }

  function getResponseContainers() {
    switch (PROVIDER) {
      case "gemini": {
        const topTurns = Array.from(document.querySelectorAll("model-response, [data-message-author-role='model'], .response-container"));
        if (topTurns.length > 0) return topTurns;
        return Array.from(document.querySelectorAll("message-content, .model-response-text, .response-container-content, div.markdown"));
      }
      case "chatgpt": {
        const topTurns = Array.from(document.querySelectorAll("div[data-message-author-role='assistant'], .agent-turn"));
        if (topTurns.length > 0) return topTurns;
        return Array.from(document.querySelectorAll("div.markdown"));
      }
      case "claude": {
        const topTurns = Array.from(document.querySelectorAll(".font-claude-message, [data-is-streaming]"));
        if (topTurns.length > 0) return topTurns;
        return Array.from(document.querySelectorAll("div.grid-cols-1"));
      }
      case "deepseek": {
        const topTurns = Array.from(document.querySelectorAll(".ds-message-content, div[class*='message-content']"));
        if (topTurns.length > 0) return topTurns;
        return Array.from(document.querySelectorAll(".ds-markdown"));
      }
      case "qwen": {
        const topTurns = Array.from(document.querySelectorAll(".chat-message-content, .message-content"));
        if (topTurns.length > 0) return topTurns;
        return Array.from(document.querySelectorAll(".qwen-markdown"));
      }
      case "grok": {
        const topTurns = Array.from(document.querySelectorAll(".message-bubble, .response-message"));
        if (topTurns.length > 0) return topTurns;
        return Array.from(document.querySelectorAll("div[class*='message']"));
      }
      default:
        return Array.from(document.querySelectorAll("[data-role='assistant'], .message"));
    }
  }

  function extractTurnText(target) {
    if (!target) return "";

    if (PROVIDER === "gemini") {
      const md = target.querySelector(
        "message-content .markdown-main-panel, " +
        "message-content, " +
        ".model-response-text, " +
        ".response-container-content .markdown, " +
        ".response-container .markdown, " +
        ".markdown"
      );
      const contentElem = md || target;

      const thoughts = contentElem.querySelector("model-thoughts, .thought-container, .thinking-process");
      if (thoughts && thoughts.parentNode) {
        try {
          const clone = contentElem.cloneNode(true);
          const cThoughts = clone.querySelector("model-thoughts, .thought-container, .thinking-process");
          if (cThoughts && cThoughts.parentNode) {
            cThoughts.parentNode.removeChild(cThoughts);
          }
          return (clone.innerText || "").trim();
        } catch (e) {
          return (contentElem.innerText || "").trim();
        }
      }
      return (contentElem.innerText || "").trim();
    }

    if (PROVIDER === "chatgpt") {
      const md = target.querySelector("div.markdown");
      return ((md || target).innerText || "").trim();
    }
    if (PROVIDER === "deepseek") {
      const md = target.querySelector(".ds-markdown");
      return ((md || target).innerText || "").trim();
    }
    if (PROVIDER === "qwen") {
      const md = target.querySelector(".qwen-markdown");
      return ((md || target).innerText || "").trim();
    }

    return (target.innerText || "").trim();
  }

  function getCurrentTurnText(beforeCount, beforeLastText) {
    const containers = getResponseContainers();
    if (containers.length === 0) return "";

    if (containers.length > beforeCount) {
      const target = containers[containers.length - 1];
      return extractTurnText(target);
    }

    const target = containers[containers.length - 1];
    const textNow = extractTurnText(target);
    if (!beforeLastText) {
      return textNow;
    }
    if (textNow === beforeLastText) {
      return "";
    }
    if (textNow.startsWith(beforeLastText)) {
      return textNow.slice(beforeLastText.length).trim();
    }
    return textNow;
  }

  function dismissPopups() {
    const popups = [
      'button:has-text("Accept all")',
      'button:has-text("Принять все")',
      'button:has-text("I agree")',
      'button:has-text("Got it")',
      'button:has-text("Понятно")',
      'button[aria-label*="Close" i]',
      'button[aria-label*="Закрыть" i]'
    ];
    for (const p of popups) {
      try {
        const btn = document.querySelector(p);
        if (btn && btn.offsetParent !== null) {
          btn.click();
        }
      } catch (e) {}
    }
  }

  let activeTask = null;

  async function executePromptTask(taskId, prompt, options = {}, onStatus, onChunk, onDone, onError) {
    if (activeTask) {
      onError({ code: "busy", message: "Another task is actively executing in this tab" });
      return;
    }

    if (isCloudflareChallenge()) {
      onError({ code: "cloudflare", message: "Обнаружена проверка Cloudflare в браузере. Пройдите проверку вручную." });
      return;
    }

    if (isNeedsAuth()) {
      onError({ code: "needs_auth", message: `Требуется авторизация в ${PROVIDER.toUpperCase()}. Пожалуйста, войдите в аккаунт.` });
      return;
    }

    dismissPopups();

    // 1. Locate input element with up to 15s wait
    onStatus({ stage: "locating_input", detail: `Locating prompt input for ${PROVIDER}` });
    const inputSelector = getInputSelector();
    let inputElem = document.querySelector(inputSelector);
    if (!inputElem) {
      let waited = 0;
      while (waited < 15000 && !inputElem) {
        await new Promise(r => setTimeout(r, 400));
        waited += 400;
        inputElem = document.querySelector(inputSelector);
      }
    }

    if (!inputElem) {
      if (isNeedsAuth()) {
        onError({ code: "needs_auth", message: `Поле ввода не найдено: требуется авторизация в ${PROVIDER.toUpperCase()}` });
        return;
      }
      onError({ code: "dom_error", message: `Не удалось обнаружить поле ввода на странице ${PROVIDER} за 15с` });
      return;
    }

    // Capture initial block count and user turns before prompt submission
    const containersBefore = getResponseContainers();
    const beforeCount = containersBefore.length;
    let beforeLastText = "";
    if (beforeCount > 0) {
      beforeLastText = extractTurnText(containersBefore[beforeCount - 1]);
    }
    const beforeUserTurns = document.querySelectorAll('user-query, [data-message-author-role="user"]').length;

    // 2. Hydrate input with prompt
    try {
      inputElem.focus();
    } catch (e) {}

    const tagName = (inputElem.tagName || "").toLowerCase();
    if (tagName === "textarea" || tagName === "input") {
      inputElem.value = prompt;
      inputElem.dispatchEvent(new Event("input", { bubbles: true }));
      inputElem.dispatchEvent(new Event("change", { bubbles: true }));
    } else {
      let inserted = false;
      try {
        inserted = document.execCommand("insertText", false, prompt);
      } catch (e) {}
      if (!inserted || !(inputElem.innerText || "").trim()) {
        inputElem.textContent = prompt;
        inputElem.dispatchEvent(new InputEvent("input", { bubbles: true }));
      }
    }

    await new Promise(r => setTimeout(r, 250));

    // 3. Wait for send button and submit
    let sendBtn = null;
    const sendWaitStart = Date.now();
    while (Date.now() - sendWaitStart < 3000) {
      sendBtn = getSendButton();
      if (sendBtn && !sendBtn.disabled && sendBtn.getAttribute("aria-disabled") !== "true") {
        break;
      }
      await new Promise(r => setTimeout(r, 150));
    }

    let sent = false;
    if (sendBtn && !sendBtn.disabled && sendBtn.getAttribute("aria-disabled") !== "true") {
      try {
        sendBtn.click();
        sent = true;
      } catch (e) {}
    }

    if (!sent) {
      inputElem.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true }));
      inputElem.dispatchEvent(new KeyboardEvent("keypress", { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true }));
      inputElem.dispatchEvent(new KeyboardEvent("keyup", { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true }));
    }

    // 4. Verification loop (up to 4s) to ensure prompt was actually submitted
    let verified = false;
    const verifyStart = Date.now();
    let retriedFallback = false;

    while (Date.now() - verifyStart < 4000) {
      const currentInputText = (inputElem.value !== undefined ? inputElem.value : (inputElem.innerText || "")).trim();
      const currentUserTurns = document.querySelectorAll('user-query, [data-message-author-role="user"]').length;
      const currentRespCount = getResponseContainers().length;
      const stopBtn = getStopButton();
      const isBusy = isGenerating();

      if (currentInputText.length === 0 || currentUserTurns > beforeUserTurns || currentRespCount > beforeCount || (stopBtn && (stopBtn.offsetParent !== null || stopBtn.getClientRects().length > 0)) || isBusy) {
        verified = true;
        break;
      }

      // If text remains in field after 1.5s, retry with Enter + re-click send button
      if (Date.now() - verifyStart > 1500 && !retriedFallback) {
        retriedFallback = true;
        inputElem.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true }));
        inputElem.dispatchEvent(new KeyboardEvent("keypress", { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true }));
        inputElem.dispatchEvent(new KeyboardEvent("keyup", { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true }));
        const retryBtn = getSendButton();
        if (retryBtn && !retryBtn.disabled) {
          try { retryBtn.click(); } catch (e) {}
        }
      }

      await new Promise(r => setTimeout(r, 200));
    }

    if (!verified) {
      const remainingText = (inputElem.value !== undefined ? inputElem.value : (inputElem.innerText || "")).trim();
      if (remainingText.length > 0) {
        onError({
          code: "submit_failed",
          message: "Не удалось отправить сообщение: кнопка отправки или Enter не очистили поле ввода"
        });
        return;
      }
    }

    // Telemetry: prompt_sent stage reached ONLY after verified submission
    onStatus({ stage: "prompt_sent", detail: "Prompt submitted to chat" });

    // 5. Dual observation (MutationObserver + 300ms polling) with 20s fail-fast
    activeTask = { taskId, cancelled: false };

    const startTime = Date.now();
    const promptSentTime = Date.now();
    const timeoutSec = options.timeout_sec || 120;
    const timeoutMs = timeoutSec * 1000;
    let lastSentLength = 0;
    let lastChangeTime = Date.now();
    let responseStarted = false;
    let finalDelivered = false;
    let unchangedTicks = 0;
    let lastStatusTime = 0;

    let pollInterval = null;
    let domObserver = null;

    function cleanup() {
      if (pollInterval) {
        clearInterval(pollInterval);
        pollInterval = null;
      }
      if (domObserver) {
        try { domObserver.disconnect(); } catch (e) {}
        domObserver = null;
      }
      activeTask = null;
    }

    function checkProgress() {
      if (!activeTask || activeTask.cancelled) {
        cleanup();
        return;
      }

      const elapsed = Date.now() - startTime;
      if (elapsed > timeoutMs) {
        cleanup();
        const currentText = getCurrentTurnText(beforeCount, beforeLastText);
        if (currentText && currentText.length > 0) {
          onDone({ full_text: currentText });
        } else {
          onError({ code: "timeout", message: `Превышено время ожидания ответа (${timeoutSec}с)` });
        }
        return;
      }

      const currentlyGenerating = isGenerating();
      const currentContainers = getResponseContainers();
      const currentCount = currentContainers.length;
      const currentText = getCurrentTurnText(beforeCount, beforeLastText);

      // 20s Fail-fast: if after prompt_sent nothing started generating and no text appeared
      if (!responseStarted) {
        const timeSinceSent = Date.now() - promptSentTime;
        if (timeSinceSent > 20000 && !currentlyGenerating && (!currentText || currentText.length === 0)) {
          cleanup();
          onError({
            code: "no_response_in_dom",
            message: `Превышено время ожидания начала ответа (20с): ${PROVIDER.toUpperCase()} не начал генерацию`
          });
          return;
        }
      }

      // Check for incoming response text
      if (currentText && currentText.length > 0) {
        if (!responseStarted) {
          responseStarted = true;
          onStatus({
            stage: "generating",
            detail: `responsesCount=${currentCount}, lastLen=${currentText.length}, isGenerating=${currentlyGenerating}`
          });
          lastStatusTime = Date.now();
        }

        if (currentText.length > lastSentLength) {
          const delta = currentText.slice(lastSentLength);
          lastSentLength = currentText.length;
          lastChangeTime = Date.now();
          unchangedTicks = 0;
          onChunk({ delta, full_text: currentText });
        } else {
          unchangedTicks++;
        }
      } else if (currentlyGenerating) {
        // Model is preparing response (thinking / waiting)
        const now = Date.now();
        if (now - lastStatusTime >= 2000) {
          lastStatusTime = now;
          onStatus({
            stage: "generating",
            detail: `responsesCount=${currentCount}, lastLen=${currentText ? currentText.length : 0}, isGenerating=${currentlyGenerating}`
          });
        }
      }

      // Finish only when generation completed AND text has stabilized
      if (responseStarted && !currentlyGenerating) {
        const stabilityMs = Date.now() - lastChangeTime;
        // Text hasn't changed for >= 1000ms and generation indicators are off, or 3 consecutive unchanged ticks
        if ((stabilityMs >= 1000 || unchangedTicks >= 3) && !finalDelivered) {
          finalDelivered = true;
          cleanup();
          onDone({ full_text: currentText });
        }
      }
    }

    // Set up MutationObserver on document.body for instant DOM reaction (even in background tabs)
    try {
      domObserver = new MutationObserver(() => {
        checkProgress();
      });
      domObserver.observe(document.body, { childList: true, subtree: true, characterData: true, attributes: true });
    } catch (obsErr) {
      console.warn("[ZCA Bridge] Could not start MutationObserver, relying on polling:", obsErr);
    }

    // Set up 300ms interval polling
    pollInterval = setInterval(checkProgress, 300);
  }

  // Handle connection from background service worker
  chrome.runtime.onConnect.addListener(port => {
    if (port.name !== "zca-bridge") return;

    port.onMessage.addListener(msg => {
      if (msg.type === "execute_task") {
        executePromptTask(
          msg.task_id,
          msg.prompt,
          { timeout_sec: msg.timeout_sec || 120 },
          (status) => {
            try { port.postMessage({ type: "status", task_id: msg.task_id, stage: status.stage, detail: status.detail }); } catch (e) {}
          },
          (chunk) => {
            try { port.postMessage({ type: "chunk", task_id: msg.task_id, delta: chunk.delta, full_text: chunk.full_text }); } catch (e) {}
          },
          (done) => {
            try { port.postMessage({ type: "done", task_id: msg.task_id, full_text: done.full_text }); } catch (e) {}
          },
          (err) => {
            try { port.postMessage({ type: "error", task_id: msg.task_id, code: err.code, message: err.message }); } catch (e) {}
          }
        );
      } else if (msg.type === "cancel") {
        if (activeTask && activeTask.taskId === msg.task_id) {
          activeTask.cancelled = true;
          activeTask = null;
        }
        // Click Stop button if visible
        try {
          const stopBtn = getStopButton();
          if (stopBtn && stopBtn.offsetParent !== null) {
            stopBtn.click();
          }
        } catch (e) {}
      }
    });

    port.onDisconnect.addListener(() => {
      if (activeTask) {
        activeTask.cancelled = true;
        activeTask = null;
      }
    });
  });

  // Handle direct ping / status query
  chrome.runtime.onMessage.addListener((req, sender, sendResponse) => {
    if (req.action === "ping_content") {
      sendResponse({
        ok: true,
        provider: PROVIDER,
        authenticated: !isNeedsAuth(),
        cloudflare: isCloudflareChallenge(),
        url: window.location.href
      });
      return true;
    }
  });
})();
