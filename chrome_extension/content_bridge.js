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
      if (bodyText.includes("войти в аккаунт") || bodyText.includes("log in to deepseek")) {
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
        return 'div.ql-editor[contenteditable="true"], rich-textarea div[contenteditable="true"], div[contenteditable="true"], textarea';
      case "chatgpt":
        return '#prompt-textarea, div#prompt-textarea[contenteditable="true"], textarea[data-id="root"], div[contenteditable="true"], textarea';
      case "claude":
        return 'div.ProseMirror[contenteditable="true"], fieldset div[contenteditable="true"], div[contenteditable="true"], textarea';
      case "deepseek":
        return '#chat-input, textarea[placeholder*="DeepSeek"], textarea[placeholder*="Сообщение"], textarea, div[contenteditable="true"]';
      case "qwen":
        return '#chat-input, textarea[placeholder*="Qwen"], textarea[placeholder*="Ask"], textarea, div[contenteditable="true"]';
      case "grok":
        return 'textarea[placeholder*="Grok"], textarea[placeholder*="Ask"], textarea, div[contenteditable="true"]';
      default:
        return 'textarea, [contenteditable="true"]';
    }
  }

  function getSendButton() {
    switch (PROVIDER) {
      case "gemini":
        return document.querySelector('button[aria-label*="Send"], button[aria-label*="Отправить"], button.send-button, button[data-testid*="send"]');
      case "chatgpt":
        return document.querySelector('button[data-testid="send-button"], button[aria-label="Send prompt"], button[data-testid*="send"]');
      case "claude":
        return document.querySelector('button[aria-label*="Send Message"], button[aria-label*="Send message"], button[aria-label*="Send"]');
      case "deepseek":
        return document.querySelector('button[aria-label*="Send"], div[role="button"][aria-label*="Send"], .ds-icon-button, button[class*="send"]');
      case "qwen":
        return document.querySelector('button[class*="send"], button[aria-label*="Send"], .ant-btn-primary');
      case "grok":
        return document.querySelector('button[aria-label*="Submit"], button[aria-label*="Grok"], button[type="submit"]');
      default:
        return document.querySelector('button[type="submit"], button[aria-label*="Send"]');
    }
  }

  function isGenerating() {
    switch (PROVIDER) {
      case "gemini": {
        const stopBtn = document.querySelector('button[aria-label*="Stop"], button[aria-label*="Остановить"], button[aria-label*="stop"], button[data-testid*="stop"]');
        const thinking = document.querySelector('model-thoughts, [aria-busy="true"], .thinking, [class*="thinking"], mat-progress-bar, .loading, [aria-label*="Loading"]');
        return Boolean(stopBtn || thinking);
      }
      case "chatgpt": {
        const stopBtn = document.querySelector('button[data-testid="stop-button"], button[aria-label*="Stop"], button[aria-label*="stop"]');
        return Boolean(stopBtn);
      }
      case "claude": {
        const stopBtn = document.querySelector('button[aria-label*="Stop Response"], button[aria-label*="Stop responding"], button[aria-label*="Stop"]');
        const streaming = document.querySelector('div[data-is-streaming="true"]');
        return Boolean(stopBtn || streaming);
      }
      case "deepseek": {
        const stopBtn = document.querySelector('button[class*="stop"], button[aria-label*="Stop"], .ds-icon-button[title*="Stop"]');
        return Boolean(stopBtn);
      }
      case "qwen": {
        const stopBtn = document.querySelector('button[class*="stop"], button[aria-label*="Stop"], .ant-btn[aria-label*="Stop"]');
        return Boolean(stopBtn);
      }
      case "grok": {
        const stopBtn = document.querySelector('button[aria-label*="Stop"], button[aria-label*="stop"]');
        return Boolean(stopBtn);
      }
      default:
        return Boolean(document.querySelector('button[aria-label*="Stop"], [aria-busy="true"]'));
    }
  }

  function extractLastResponse() {
    let selector = "";
    switch (PROVIDER) {
      case "gemini":
        selector = "message-content, .model-response-text, .response-container-content, [data-message-author-role='model']";
        break;
      case "chatgpt":
        selector = "div[data-message-author-role='assistant'], .agent-turn, div.markdown";
        break;
      case "claude":
        selector = ".font-claude-message, div.grid-cols-1";
        break;
      case "deepseek":
        selector = ".ds-markdown, .ds-message-content, div[class*='message-content']";
        break;
      case "qwen":
        selector = ".qwen-markdown, .chat-message-content, .message-content";
        break;
      case "grok":
        selector = ".message-bubble, .response-message, div[class*='message']";
        break;
      default:
        selector = "[data-role='assistant'], .message";
    }

    const elements = Array.from(document.querySelectorAll(selector));
    if (elements.length === 0) return "";
    const lastElem = elements[elements.length - 1];
    return (lastElem.innerText || "").trim();
  }

  function dismissPopups() {
    const popups = [
      'button:has-text("Accept all")',
      'button:has-text("Принять все")',
      'button:has-text("I agree")',
      'button:has-text("Got it")',
      'button:has-text("Понятно")',
      'button[aria-label*="Close"]',
      'button[aria-label*="Закрыть"]'
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

  async function executePromptTask(taskId, prompt, options = {}, onChunk, onDone, onError) {
    if (activeTask) {
      onError({ code: "busy", message: "Another task is actively executing in this tab" });
      return;
    }

    if (isCloudflareChallenge()) {
      onError({ code: "cloudflare", message: "Обнаружена проверка Cloudflare в браузере. Пройдите проверку вручную." });
      return;
    }

    if (isNeedsAuth()) {
      onError({ code: "needs_auth", message: `Требуется авторизация в ${PROVIDER}. Пожалуйста, войдите в аккаунт.` });
      return;
    }

    dismissPopups();

    // 1. Locate input element
    const inputSelector = getInputSelector();
    let inputElem = document.querySelector(inputSelector);
    if (!inputElem) {
      let waited = 0;
      while (waited < 5000 && !inputElem) {
        await new Promise(r => setTimeout(r, 400));
        waited += 400;
        inputElem = document.querySelector(inputSelector);
      }
    }

    if (!inputElem) {
      if (isNeedsAuth()) {
        onError({ code: "needs_auth", message: `Поле ввода не найдено: требуется авторизация в ${PROVIDER}` });
        return;
      }
      onError({ code: "dom_error", message: `Не удалось обнаружить поле ввода на странице ${PROVIDER}` });
      return;
    }

    // Baseline response before sending
    const baselineText = extractLastResponse();

    // 2. Set value
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
        inputElem.innerText = prompt;
        inputElem.dispatchEvent(new Event("input", { bubbles: true }));
      }
    }

    await new Promise(r => setTimeout(r, 300));

    // 3. Click Send or dispatch Enter
    let sendBtn = getSendButton();
    let sent = false;
    if (sendBtn && !sendBtn.disabled) {
      try {
        sendBtn.click();
        sent = true;
      } catch (e) {}
    }

    if (!sent) {
      inputElem.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true }));
    }

    // 4. Response monitoring loop
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
        const currentText = extractLastResponse();
        activeTask = null;
        if (currentText && currentText !== baselineText) {
          onDone({ full_text: currentText });
        } else {
          onError({ code: "timeout", message: `Превышено время ожидания ответа (${timeoutSec}с)` });
        }
        return;
      }

      const currentlyGenerating = isGenerating();
      const currentText = extractLastResponse();

      if (currentText && currentText !== baselineText) {
        if (!responseStarted) {
          responseStarted = true;
        }

        if (currentText.length > lastSentLength) {
          const delta = currentText.slice(lastSentLength);
          lastSentLength = currentText.length;
          lastChangeTime = Date.now();
          onChunk({ delta, full_text: currentText });
        }
      }

      // If generation finished (stop button disappeared, thoughts complete)
      if (responseStarted && !currentlyGenerating) {
        const stabilityMs = Date.now() - lastChangeTime;
        // Require 1.5 seconds of silence without new characters
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

    console.log("[ZCA ContentBridge] Port connected");

    port.onMessage.addListener(msg => {
      if (msg.type === "execute_task") {
        executePromptTask(
          msg.task_id,
          msg.prompt,
          { timeout_sec: msg.timeout_sec || 120 },
          (chunk) => {
            port.postMessage({ type: "chunk", task_id: msg.task_id, delta: chunk.delta, full_text: chunk.full_text });
          },
          (done) => {
            port.postMessage({ type: "done", task_id: msg.task_id, full_text: done.full_text });
          },
          (err) => {
            port.postMessage({ type: "error", task_id: msg.task_id, code: err.code, message: err.message });
          }
        );
      } else if (msg.type === "cancel") {
        if (activeTask && activeTask.taskId === msg.task_id) {
          activeTask.cancelled = true;
          activeTask = null;
        }
      } else if (msg.type === "check_status") {
        port.postMessage({
          type: "status",
          provider: PROVIDER,
          authenticated: !isNeedsAuth(),
          cloudflare: isCloudflareChallenge(),
          url: window.location.href
        });
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
    if (req.action === "probe_tab") {
      sendResponse({
        provider: PROVIDER,
        authenticated: !isNeedsAuth(),
        cloudflare: isCloudflareChallenge(),
        url: window.location.href
      });
      return true;
    }
  });
})();
