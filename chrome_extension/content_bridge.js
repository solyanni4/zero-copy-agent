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
      case "gemini":
        return document.querySelector(
          'button.send-button:not([disabled]), ' +
          'button[aria-label*="Send" i]:not([disabled]), ' +
          'button[aria-label*="Отправить" i]:not([disabled]), ' +
          'button[aria-label*="Submit" i]:not([disabled]), ' +
          'button[data-testid*="send" i]:not([disabled])'
        );
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
      case "gemini":
        return document.querySelector('button[aria-label*="Stop" i], button[aria-label*="Остановить" i], button[data-testid*="stop" i], .stop-icon');
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
    if (stopBtn && stopBtn.offsetParent !== null) {
      return true;
    }

    switch (PROVIDER) {
      case "gemini": {
        const thinking = document.querySelector('model-thoughts, [aria-busy="true"], .thinking, [class*="thinking"], mat-progress-bar, .loading, [aria-label*="Loading" i]');
        return Boolean(thinking);
      }
      case "claude": {
        const streaming = document.querySelector('div[data-is-streaming="true"]');
        return Boolean(streaming);
      }
      case "chatgpt": {
        const busy = document.querySelector('.result-streaming, [data-is-streaming="true"]');
        return Boolean(busy);
      }
      default:
        return Boolean(document.querySelector('[aria-busy="true"], .result-streaming, .loading'));
    }
  }

  function getResponseContainers() {
    switch (PROVIDER) {
      case "gemini":
        return Array.from(document.querySelectorAll("model-response, message-content, [data-message-author-role='model'], .response-container-content"));
      case "chatgpt":
        return Array.from(document.querySelectorAll("div[data-message-author-role='assistant'], .agent-turn, div.markdown"));
      case "claude":
        return Array.from(document.querySelectorAll(".font-claude-message, div.grid-cols-1"));
      case "deepseek":
        return Array.from(document.querySelectorAll(".ds-markdown, .ds-message-content, div[class*='message-content']"));
      case "qwen":
        return Array.from(document.querySelectorAll(".qwen-markdown, .chat-message-content, .message-content"));
      case "grok":
        return Array.from(document.querySelectorAll(".message-bubble, .response-message, div[class*='message']"));
      default:
        return Array.from(document.querySelectorAll("[data-role='assistant'], .message"));
    }
  }

  function extractResponseFromTurn(initialBlockCount) {
    const containers = getResponseContainers();
    if (containers.length === 0) return "";

    let target = null;
    if (containers.length > initialBlockCount) {
      target = containers[containers.length - 1];
    } else {
      target = containers[containers.length - 1];
    }

    if (!target) return "";

    if (PROVIDER === "gemini") {
      const md = target.querySelector(".markdown, message-content, .model-response-text");
      return ((md || target).innerText || "").trim();
    }
    return (target.innerText || "").trim();
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

    // Capture initial block count before prompt submission
    const initialBlockCount = getResponseContainers().length;
    const baselineText = extractResponseFromTurn(initialBlockCount);

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

    await new Promise(r => setTimeout(r, 200));

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
      inputElem.dispatchEvent(new KeyboardEvent("keydown", {
        key: "Enter",
        code: "Enter",
        keyCode: 13,
        which: 13,
        bubbles: true
      }));
    }

    // Telemetry: prompt_sent stage reached
    onStatus({ stage: "prompt_sent", detail: "Prompt submitted to chat" });

    // 4. Monitoring loop with non-premature exit
    activeTask = { taskId, cancelled: false };

    const startTime = Date.now();
    const timeoutSec = options.timeout_sec || 120;
    const timeoutMs = timeoutSec * 1000;
    let lastSentLength = 0;
    let lastChangeTime = Date.now();
    let responseStarted = false;
    let finalDelivered = false;

    const pollInterval = setInterval(() => {
      if (!activeTask || activeTask.cancelled) {
        clearInterval(pollInterval);
        activeTask = null;
        return;
      }

      const elapsed = Date.now() - startTime;
      if (elapsed > timeoutMs) {
        clearInterval(pollInterval);
        const currentText = extractResponseFromTurn(initialBlockCount);
        activeTask = null;
        if (currentText && currentText !== baselineText) {
          onDone({ full_text: currentText });
        } else {
          onError({ code: "timeout", message: `Превышено время ожидания ответа (${timeoutSec}с)` });
        }
        return;
      }

      const currentlyGenerating = isGenerating();
      const currentText = extractResponseFromTurn(initialBlockCount);

      if (currentText && currentText !== baselineText) {
        if (!responseStarted) {
          responseStarted = true;
          onStatus({ stage: "generating", detail: "First response token received" });
        }

        if (currentText.length > lastSentLength) {
          const delta = currentText.slice(lastSentLength);
          lastSentLength = currentText.length;
          lastChangeTime = Date.now();
          onChunk({ delta, full_text: currentText });
        }
      } else if (currentlyGenerating && !responseStarted) {
        // Model is thinking or preparing response
        lastChangeTime = Date.now();
      }

      // Finish only when generation completed AND text has stabilized for 1.5s
      if (responseStarted && !currentlyGenerating) {
        const stabilityMs = Date.now() - lastChangeTime;
        if (stabilityMs >= 1500 && !finalDelivered) {
          finalDelivered = true;
          clearInterval(pollInterval);
          activeTask = null;
          onDone({ full_text: currentText });
        }
      }
    }, 200);
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
