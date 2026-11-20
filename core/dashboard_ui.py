"""
Dashboard UI Renderer for zero-copy-agent.
Provides a modern, calm, human-friendly desktop interface with tabbed navigation:
- Token Economics & Monitoring (Actual Processed vs Baseline Without Agent, Savings Meter)
- Providers & Web Sessions (1-Click Browser Login for Gemini/ChatGPT/Claude, API Keys)
- IDE & Agent Integration (1-Click Copy Base URL, MCP SSE, Auto-Setup)
- Playground (Interactive Testing with DLP, BPE, and Cascades)
- Live Event Stream (Audit & Telemetry Log)
"""
from typing import Dict, Any


def render_dashboard_html(
    stats: Dict[str, Any],
    version: str,
    port: int,
    ollama_status_badge: str,
    cloud_gateway_badge: str = "Gemini 4 Neon / Adv"
) -> str:
    total_saved = stats.get("total_saved_usd", 0.0)
    total_tokens = f"{stats.get('total_tokens', 0):,}"
    tokens_without_zc = f"{stats.get('tokens_without_zc', 0):,}"
    tokens_saved = f"{stats.get('tokens_saved', 0):,}"
    tokens_saved_pct = stats.get("tokens_saved_pct", 0.0)
    today_saved = stats.get("today_saved_usd", 0.0)
    total_requests = stats.get("total_requests", 0)

    # Percentage bar calculation
    actual_pct = max(4, min(100, int(100 - tokens_saved_pct))) if stats.get("tokens_without_zc", 0) > 0 else 100

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>zero-copy-agent</title>
    <style>
        :root {{
            --bg-base: #0b0f17;
            --bg-card: #131924;
            --bg-card-hover: #172030;
            --bg-input: #0e141f;
            --border: #1e293b;
            --border-focus: #38bdf8;
            --text-main: #f1f5f9;
            --text-muted: #94a3b8;
            --text-dim: #64748b;
            --accent-blue: #38bdf8;
            --accent-blue-hover: #0ea5e9;
            --accent-emerald: #10b981;
            --accent-amber: #f59e0b;
            --accent-red: #ef4444;
            --accent-purple: #a855f7;
            --radius-sm: 6px;
            --radius-md: 10px;
            --radius-lg: 14px;
        }}

        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            user-select: text;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background-color: var(--bg-base);
            color: var(--text-main);
            line-height: 1.5;
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            overflow-x: hidden;
        }}

        /* Header */
        header {{
            background: var(--bg-card);
            border-bottom: 1px solid var(--border);
            padding: 14px 28px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            position: sticky;
            top: 0;
            z-index: 100;
        }}

        .brand {{
            display: flex;
            align-items: center;
            gap: 12px;
        }}

        .brand-logo {{
            width: 32px;
            height: 32px;
            border-radius: 8px;
            background: linear-gradient(135deg, #0284c7, #10b981);
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 800;
            font-size: 16px;
            color: #fff;
            box-shadow: 0 0 16px rgba(16, 185, 129, 0.25);
        }}

        .brand-title {{
            font-size: 17px;
            font-weight: 700;
            letter-spacing: -0.3px;
            color: var(--text-main);
        }}

        .brand-tag {{
            font-size: 11px;
            padding: 2px 7px;
            border-radius: 12px;
            background: rgba(56, 189, 248, 0.12);
            color: var(--accent-blue);
            border: 1px solid rgba(56, 189, 248, 0.3);
            font-weight: 600;
        }}

        .header-actions {{
            display: flex;
            align-items: center;
            gap: 12px;
        }}

        .status-pill {{
            display: flex;
            align-items: center;
            gap: 7px;
            font-size: 12px;
            padding: 5px 12px;
            border-radius: 20px;
            background: rgba(16, 185, 129, 0.1);
            color: var(--accent-emerald);
            border: 1px solid rgba(16, 185, 129, 0.25);
            font-weight: 500;
        }}

        .status-dot {{
            width: 7px;
            height: 7px;
            border-radius: 50%;
            background: var(--accent-emerald);
            box-shadow: 0 0 8px var(--accent-emerald);
        }}

        .btn-header {{
            background: var(--bg-base);
            border: 1px solid var(--border);
            color: var(--text-muted);
            padding: 6px 12px;
            border-radius: var(--radius-sm);
            font-size: 12px;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.15s ease;
            display: flex;
            align-items: center;
            gap: 6px;
        }}

        .btn-header:hover {{
            color: var(--text-main);
            border-color: var(--text-dim);
            background: var(--bg-card-hover);
        }}

        /* Navigation Tabs */
        .nav-bar {{
            background: var(--bg-card);
            border-bottom: 1px solid var(--border);
            padding: 0 28px;
            display: flex;
            gap: 6px;
            overflow-x: auto;
        }}

        .nav-tab {{
            padding: 12px 18px;
            font-size: 13px;
            font-weight: 600;
            color: var(--text-muted);
            border-bottom: 2px solid transparent;
            cursor: pointer;
            transition: all 0.15s ease;
            white-space: nowrap;
            background: none;
            border-top: none;
            border-left: none;
            border-right: none;
        }}

        .nav-tab:hover {{
            color: var(--text-main);
        }}

        .nav-tab.active {{
            color: var(--accent-blue);
            border-bottom-color: var(--accent-blue);
        }}

        /* Main Content Container */
        .content-wrap {{
            max-width: 1100px;
            width: 100%;
            margin: 0 auto;
            padding: 24px 28px 48px 28px;
            flex: 1;
        }}

        .tab-content {{
            display: none;
        }}

        .tab-content.active {{
            display: block;
        }}

        /* Cards & Grids */
        .card {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: var(--radius-md);
            padding: 22px;
            margin-bottom: 20px;
        }}

        .card-title {{
            font-size: 16px;
            font-weight: 600;
            color: var(--text-main);
            margin-bottom: 6px;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }}

        .card-desc {{
            font-size: 13px;
            color: var(--text-muted);
            margin-bottom: 18px;
            line-height: 1.5;
        }}

        .grid-4 {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
            gap: 14px;
            margin-bottom: 20px;
        }}

        .metric-card {{
            background: var(--bg-base);
            border: 1px solid var(--border);
            border-radius: var(--radius-sm);
            padding: 16px;
            transition: border-color 0.2s ease;
        }}

        .metric-card:hover {{
            border-color: var(--text-dim);
        }}

        .metric-subhead {{
            font-size: 11px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.6px;
            color: var(--text-dim);
            margin-bottom: 6px;
        }}

        .metric-value {{
            font-size: 26px;
            font-weight: 700;
            color: var(--text-main);
            letter-spacing: -0.5px;
            font-variant-numeric: tabular-nums;
        }}

        .metric-value.emerald {{
            color: var(--accent-emerald);
        }}

        .metric-value.blue {{
            color: var(--accent-blue);
        }}

        .metric-hint {{
            font-size: 12px;
            color: var(--text-muted);
            margin-top: 5px;
        }}

        /* Savings Comparison Meter */
        .savings-meter-card {{
            background: var(--bg-base);
            border: 1px solid var(--border);
            border-radius: var(--radius-sm);
            padding: 18px 20px;
            margin-bottom: 20px;
        }}

        .meter-row {{
            margin-bottom: 14px;
        }}

        .meter-header {{
            display: flex;
            justify-content: space-between;
            font-size: 12px;
            font-weight: 550;
            margin-bottom: 6px;
        }}

        .meter-track {{
            height: 9px;
            background: #1e293b;
            border-radius: 5px;
            overflow: hidden;
            position: relative;
        }}

        .meter-fill-baseline {{
            height: 100%;
            width: 100%;
            background: linear-gradient(90deg, #94a3b8, #ef4444);
            border-radius: 5px;
        }}

        .meter-fill-actual {{
            height: 100%;
            width: {actual_pct}%;
            background: linear-gradient(90deg, #0284c7, #10b981);
            border-radius: 5px;
            transition: width 0.5s ease;
        }}

        .meter-summary-text {{
            font-size: 12.5px;
            color: var(--text-muted);
            line-height: 1.5;
            margin-top: 10px;
            padding-top: 10px;
            border-top: 1px solid #1e293b;
        }}

        /* Buttons & Inputs */
        .btn {{
            background: var(--accent-blue);
            color: #0b0f17;
            padding: 8px 16px;
            border-radius: var(--radius-sm);
            font-size: 13px;
            font-weight: 600;
            border: 1px solid transparent;
            cursor: pointer;
            transition: all 0.15s ease;
            display: inline-flex;
            align-items: center;
            gap: 7px;
            text-decoration: none;
        }}

        .btn:hover {{
            background: var(--accent-blue-hover);
        }}

        .btn-outline {{
            background: transparent;
            border: 1px solid var(--border);
            color: var(--text-main);
        }}

        .btn-outline:hover {{
            border-color: var(--text-dim);
            background: var(--bg-card-hover);
        }}

        .btn-sm {{
            padding: 4px 10px;
            font-size: 11.5px;
        }}

        .btn-danger {{
            background: transparent;
            border: 1px solid var(--accent-red);
            color: var(--accent-red);
        }}

        .btn-danger:hover {{
            background: rgba(239, 68, 68, 0.15);
        }}

        /* Quick-copy rows */
        .copy-row {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            background: var(--bg-base);
            border: 1px solid var(--border);
            border-radius: var(--radius-sm);
            padding: 10px 14px;
            margin-bottom: 10px;
            gap: 12px;
        }}

        .copy-meta {{
            display: flex;
            flex-direction: column;
            gap: 2px;
            overflow: hidden;
        }}

        .copy-label {{
            font-size: 11px;
            color: var(--text-dim);
            text-transform: uppercase;
            font-weight: 600;
            letter-spacing: 0.5px;
        }}

        .copy-value {{
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
            font-size: 13px;
            color: var(--accent-blue);
            word-break: break-all;
        }}

        /* Tables */
        .app-table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
            margin-top: 10px;
        }}

        .app-table th {{
            text-align: left;
            padding: 10px 12px;
            color: var(--text-dim);
            font-size: 11.5px;
            text-transform: uppercase;
            font-weight: 600;
            border-bottom: 1px solid var(--border);
        }}

        .app-table td {{
            padding: 12px;
            border-bottom: 1px solid rgba(30, 41, 59, 0.7);
            color: var(--text-main);
        }}

        .app-table tr:hover td {{
            background: rgba(255, 255, 255, 0.02);
        }}

        /* Provider Cards Grid */
        .provider-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(290px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }}

        .provider-box {{
            background: var(--bg-base);
            border: 1px solid var(--border);
            border-radius: var(--radius-sm);
            padding: 18px;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            min-height: 150px;
        }}

        .provider-header {{
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 8px;
        }}

        .provider-name {{
            font-size: 14.5px;
            font-weight: 600;
            color: var(--text-main);
        }}

        .provider-status {{
            font-size: 11px;
            font-weight: 600;
            padding: 3px 8px;
            border-radius: 12px;
        }}

        .status-active {{
            background: rgba(16, 185, 129, 0.12);
            color: var(--accent-emerald);
            border: 1px solid rgba(16, 185, 129, 0.25);
        }}

        .status-idle {{
            background: rgba(148, 163, 184, 0.1);
            color: var(--text-muted);
            border: 1px solid var(--border);
        }}

        /* Toast notifications */
        .toast {{
            position: fixed;
            bottom: 24px;
            right: 24px;
            background: var(--bg-card);
            border: 1px solid var(--border);
            color: var(--text-main);
            padding: 12px 18px;
            border-radius: var(--radius-sm);
            font-size: 13px;
            box-shadow: 0 10px 25px rgba(0,0,0,0.5);
            display: none;
            z-index: 1000;
            max-width: 420px;
            line-height: 1.4;
        }}

        .toast.show {{
            display: block;
            animation: slideUp 0.2s ease;
        }}

        .toast-success {{
            border-left: 4px solid var(--accent-emerald);
        }}

        .toast-info {{
            border-left: 4px solid var(--accent-blue);
        }}

        .toast-danger {{
            border-left: 4px solid var(--accent-red);
        }}

        @keyframes slideUp {{
            from {{ transform: translateY(12px); opacity: 0; }}
            to {{ transform: translateY(0); opacity: 1; }}
        }}
    </style>
</head>
<body>

    <!-- Header -->
    <header>
        <div class="brand">
            <div class="brand-logo">Z</div>
            <div>
                <span class="brand-title">zero-copy-agent</span>
                <span class="brand-tag">v{version}</span>
            </div>
        </div>
        <div class="header-actions">
            <div class="status-pill">
                <span class="status-dot"></span>
                <span>Порт {port} активен</span>
            </div>
            <button class="btn-header" onclick="minimizeToTray()">Свернуть в трей</button>
            <button class="btn-header" onclick="quitApp()">Выход</button>
        </div>
    </header>

    <!-- Navigation Tabs -->
    <div class="nav-bar">
        <button class="nav-tab active" id="nav-dashboard" onclick="switchTab('dashboard')">Мониторинг и токены</button>
        <button class="nav-tab" id="nav-providers" onclick="switchTab('providers')">Провайдеры и веб-сессии</button>
        <button class="nav-tab" id="nav-ide" onclick="switchTab('ide')">Подключение к IDE</button>
        <button class="nav-tab" id="nav-playground" onclick="switchTab('playground')">Песочница</button>
        <button class="nav-tab" id="nav-logs" onclick="switchTab('logs')">Журнал событий</button>
    </div>

    <!-- Main Container -->
    <div class="content-wrap">

        <!-- TAB 1: Мониторинг и токены -->
        <div class="tab-content active" id="tab-dashboard">
            <div class="card">
                <div class="card-title">
                    <span>Экономика токенов</span>
                    <span style="font-size: 12px; font-weight: normal; color: var(--text-dim);">Всего запросов: {total_requests}</span>
                </div>
                <div class="card-desc">
                    Сравнение фактического расхода с сырым объемом без оптимизации. Шлюз вырезает шум, скрывает секреты и перенаправляет рутину на локальную модель.
                </div>

                <div class="grid-4">
                    <div class="metric-card">
                        <div class="metric-subhead">Фактически обработано</div>
                        <div class="metric-value blue">{total_tokens}</div>
                        <div class="metric-hint">Отправлено в модели со шлюзом</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-subhead">Расход без агента</div>
                        <div class="metric-value" style="color: var(--accent-red); text-decoration: line-through;">{tokens_without_zc}</div>
                        <div class="metric-hint">Было бы сожжено без сжатия</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-subhead">Сэкономлено токенов</div>
                        <div class="metric-value emerald">-{tokens_saved}</div>
                        <div class="metric-hint">Чистая экономия: -{tokens_saved_pct}%</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-subhead">Сэкономлено на API</div>
                        <div class="metric-value emerald">${total_saved:.2f}</div>
                        <div class="metric-hint">Сегодня сохранено: ${today_saved:.2f}</div>
                    </div>
                </div>

                <!-- Визуальная шкала -->
                <div class="savings-meter-card">
                    <div class="meter-row">
                        <div class="meter-header">
                            <span style="color: var(--text-muted);">Расход без шлюза (100%):</span>
                            <span style="font-family: monospace; color: var(--accent-red);">{tokens_without_zc} токенов</span>
                        </div>
                        <div class="meter-track">
                            <div class="meter-fill-baseline"></div>
                        </div>
                    </div>

                    <div class="meter-row" style="margin-bottom: 0;">
                        <div class="meter-header">
                            <span style="color: var(--text-muted);">Фактический расход со шлюзом ({actual_pct}%):</span>
                            <span style="font-family: monospace; color: var(--accent-emerald);">{total_tokens} токенов</span>
                        </div>
                        <div class="meter-track">
                            <div class="meter-fill-actual"></div>
                        </div>
                    </div>

                    <div class="meter-summary-text">
                        Сэкономлено <strong>{tokens_saved} токенов ({tokens_saved_pct}%)</strong>. Локальное сжатие контекста и вырезание избыточных файлов защищают баланс и не засоряют контекстное окно ИИ.
                    </div>
                </div>

                <!-- Статус рабочих контуров -->
                <div class="grid-4" style="margin-bottom: 0;">
                    <div class="metric-card">
                        <div class="metric-subhead">Облачный шлюз</div>
                        <div style="font-size: 14px; font-weight: 600; color: var(--accent-emerald); margin-top: 6px;">{cloud_gateway_badge}</div>
                        <div class="metric-hint">Основная модель глубокого анализа</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-subhead">Локальная видеокарта</div>
                        <div style="font-size: 14px; font-weight: 600; color: var(--accent-blue); margin-top: 6px;">{ollama_status_badge}</div>
                        <div class="metric-hint">0$ за правки и автокомплит</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-subhead">Защита секретов (DLP)</div>
                        <div style="font-size: 14px; font-weight: 600; color: var(--accent-emerald); margin-top: 6px;">Маскировка активна</div>
                        <div class="metric-hint">Пароли и ключи не уходят в облако</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-subhead">MCP-сервер</div>
                        <div style="font-size: 14px; font-weight: 600; color: var(--accent-purple); margin-top: 6px;">SSE мост готов</div>
                        <div class="metric-hint">Прямой доступ для IDE-агентов</div>
                    </div>
                </div>
            </div>
        </div>

        <!-- TAB 2: Провайдеры и веб-сессии -->
        <div class="tab-content" id="tab-providers">
            <div class="card">
                <div class="card-title">Подключение провайдеров</div>
                <div class="card-desc">
                    Используйте браузерную веб-сессию подписки или прямой API-ключ. Нажмите «Войти через браузер», войдите в аккаунт, и сессия сохранится автоматически.
                </div>

                <div class="provider-grid" id="providers-status-grid">
                    <div class="provider-box">
                        <div class="provider-header">
                            <div>
                                <div class="provider-name">Google Gemini</div>
                                <div style="font-size: 12px; color: var(--text-dim); margin-top: 2px;">Gemini Advanced / AI Studio</div>
                            </div>
                            <span class="provider-status status-idle" id="badge-gemini">Проверка...</span>
                        </div>
                        <div style="margin-top: auto; display: flex; gap: 8px;">
                            <button class="btn btn-sm" onclick="triggerBrowserLogin('gemini')">Войти через браузер</button>
                        </div>
                    </div>

                    <div class="provider-box">
                        <div class="provider-header">
                            <div>
                                <div class="provider-name">OpenAI (ChatGPT)</div>
                                <div style="font-size: 12px; color: var(--text-dim); margin-top: 2px;">ChatGPT Plus / Free / API</div>
                            </div>
                            <span class="provider-status status-idle" id="badge-chatgpt">Проверка...</span>
                        </div>
                        <div style="margin-top: auto; display: flex; gap: 8px;">
                            <button class="btn btn-sm" onclick="triggerBrowserLogin('chatgpt')">Войти через браузер</button>
                        </div>
                    </div>

                    <div class="provider-box">
                        <div class="provider-header">
                            <div>
                                <div class="provider-name">Anthropic Claude</div>
                                <div style="font-size: 12px; color: var(--text-dim); margin-top: 2px;">Claude Pro / API</div>
                            </div>
                            <span class="provider-status status-idle" id="badge-claude">Проверка...</span>
                        </div>
                        <div style="margin-top: auto; display: flex; gap: 8px;">
                            <button class="btn btn-sm" onclick="triggerBrowserLogin('claude')">Войти через браузер</button>
                        </div>
                    </div>
                </div>

                <!-- Добавление API-ключа вручную -->
                <div style="background: var(--bg-base); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 18px; margin-bottom: 20px;">
                    <div style="font-size: 14px; font-weight: 600; margin-bottom: 10px;">Добавить прямой API-ключ:</div>
                    <div style="display: flex; gap: 10px; flex-wrap: wrap;">
                        <select id="new-key-provider" style="background: var(--bg-card); border: 1px solid var(--border); color: var(--text-main); padding: 8px 12px; border-radius: var(--radius-sm); font-size: 13px;">
                            <option value="gemini">Google Gemini API</option>
                            <option value="chatgpt">OpenAI (ChatGPT API)</option>
                            <option value="claude">Anthropic Claude API</option>
                            <option value="deepseek">DeepSeek API</option>
                        </select>
                        <input type="password" id="new-key-value" placeholder="Вставьте API-ключ (AIza... или sk-...)" style="flex: 2; min-width: 200px; background: var(--bg-card); border: 1px solid var(--border); color: var(--text-main); padding: 8px 12px; border-radius: var(--radius-sm); font-size: 13px;" />
                        <input type="text" id="new-key-alias" placeholder="Название (например: Рабочий)" style="flex: 1; min-width: 130px; background: var(--bg-card); border: 1px solid var(--border); color: var(--text-main); padding: 8px 12px; border-radius: var(--radius-sm); font-size: 13px;" />
                        <button class="btn" onclick="saveNewKey()">Сохранить ключ</button>
                    </div>
                </div>

                <!-- Список активных аккаунтов -->
                <div style="font-size: 14px; font-weight: 600; margin-bottom: 8px;">Сохраненные профили и ключи:</div>
                <div id="accounts-table-container">
                    <div style="color: var(--text-dim); font-size: 13px;">Загрузка списка аккаунтов...</div>
                </div>
            </div>
        </div>

        <!-- TAB 3: Подключение к IDE -->
        <div class="tab-content" id="tab-ide">
            <div class="card">
                <div class="card-title">Быстрое подключение к редакторам</div>
                <div class="card-desc">
                    Скопируйте адрес шлюза в настройки любого OpenAI-совместимого клиента или подключите агент через MCP.
                </div>

                <div class="copy-row">
                    <div class="copy-meta">
                        <span class="copy-label">OpenAI-совместимый Base URL (для Cursor, Aider, Cline)</span>
                        <span class="copy-value">http://127.0.0.1:{port}/v1</span>
                    </div>
                    <button class="btn btn-outline btn-sm" onclick="copyText('http://127.0.0.1:{port}/v1', 'Base URL')">Копировать</button>
                </div>

                <div class="copy-row">
                    <div class="copy-meta">
                        <span class="copy-label">MCP SSE Endpoint (для Antigravity, Cursor, Claude Desktop)</span>
                        <span class="copy-value">http://127.0.0.1:{port}/sse</span>
                    </div>
                    <button class="btn btn-outline btn-sm" onclick="copyText('http://127.0.0.1:{port}/sse', 'MCP URL')">Копировать</button>
                </div>

                <div class="copy-row">
                    <div class="copy-meta">
                        <span class="copy-label">API-ключ (произвольный)</span>
                        <span class="copy-value">sk-zero-copy</span>
                    </div>
                    <button class="btn btn-outline btn-sm" onclick="copyText('sk-zero-copy', 'API Key')">Копировать</button>
                </div>

                <div style="margin-top: 24px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                        <div style="font-size: 14px; font-weight: 600;">Обнаруженные редакторы кода:</div>
                        <button class="btn btn-outline btn-sm" onclick="autoSetupAllIDEs()">Настроить все в 1 клик</button>
                    </div>
                    <div id="ide-scan-container">
                        <div style="color: var(--text-dim); font-size: 13px;">Сканирование установленных сред...</div>
                    </div>
                </div>

                <!-- Конфигурация MCP JSON -->
                <div style="margin-top: 24px; background: var(--bg-base); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 16px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                        <span style="font-size: 12px; font-weight: 600; color: var(--text-dim); text-transform: uppercase;">Конфиг для mcp_config.json:</span>
                        <button class="btn btn-outline btn-sm" onclick="copyMcpJson()">Копировать JSON</button>
                    </div>
                    <pre style="font-family: monospace; font-size: 12.5px; color: var(--accent-blue); overflow-x: auto;">"zero-copy-agent": {{
  "url": "http://127.0.0.1:{port}/sse"
}}</pre>
                </div>
            </div>
        </div>

        <!-- TAB 4: Песочница -->
        <div class="tab-content" id="tab-playground">
            <div class="card">
                <div class="card-title">Проверка запросов и фильтров</div>
                <div class="card-desc">
                    Отправьте тестовый запрос, чтобы в реальном времени оценить маскировку паролей (DLP), сжатие контекста и ответ модели.
                </div>

                <div style="display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 14px;">
                    <span style="font-size: 12px; color: var(--text-dim); align-self: center;">Быстрые тесты:</span>
                    <button class="btn-header" onclick="setPlaygroundTemplate('dlp')">Тест маскировки паролей</button>
                    <button class="btn-header" onclick="setPlaygroundTemplate('code')">Тест генерации кода</button>
                    <button class="btn-header" onclick="setPlaygroundTemplate('cascade')">Тест каскада Архитектора</button>
                </div>

                <div style="display: flex; gap: 10px; align-items: center; margin-bottom: 12px;">
                    <span style="font-size: 13px; font-weight: 600; color: var(--text-muted);">Модель:</span>
                    <select id="play-model-select" style="background: var(--bg-base); border: 1px solid var(--border); color: var(--accent-blue); font-weight: 600; padding: 6px 12px; border-radius: var(--radius-sm); font-size: 13px;">
                        <option value="gemini-4-neon">Загрузка моделей...</option>
                    </select>
                </div>

                <textarea id="play-prompt" rows="4" style="width: 100%; background: var(--bg-base); border: 1px solid var(--border); color: var(--text-main); padding: 12px; border-radius: var(--radius-sm); font-family: monospace; font-size: 13px; resize: vertical;" placeholder="Введите промпт для проверки..."></textarea>

                <div style="margin-top: 12px; display: flex; justify-content: space-between; align-items: center;">
                    <button class="btn" id="play-submit-btn" onclick="runPlayground()">Отправить запрос</button>
                    <span id="play-status" style="font-size: 12.5px; color: var(--text-muted);"></span>
                </div>

                <div id="play-result-box" style="display: none; margin-top: 16px; padding: 16px; background: var(--bg-base); border: 1px solid var(--border); border-radius: var(--radius-sm);">
                    <div style="display: flex; gap: 16px; margin-bottom: 10px; font-size: 12px; border-bottom: 1px solid var(--border); padding-bottom: 8px;">
                        <span id="stat-duration" style="color: var(--accent-blue); font-weight: 600;">Время: -</span>
                        <span id="stat-dlp" style="color: var(--accent-emerald); font-weight: 600;">DLP: -</span>
                        <span id="stat-bpe" style="color: var(--accent-purple); font-weight: 600;">BPE: -</span>
                    </div>
                    <div id="play-output" style="white-space: pre-wrap; font-size: 13px; line-height: 1.5; color: var(--text-main);"></div>
                </div>
            </div>
        </div>

        <!-- TAB 5: Журнал событий -->
        <div class="tab-content" id="tab-logs">
            <div class="card">
                <div class="card-title">
                    <span>Поток событий конвейера</span>
                    <button class="btn-header" onclick="fetchEvents()">Обновить</button>
                </div>
                <div class="card-desc">
                    События обработки запросов: маскировка паролей, сжатие длинных файлов и перенаправление между локальной и облачной моделями.
                </div>

                <div style="max-height: 480px; overflow-y: auto; background: var(--bg-base); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 14px; font-family: ui-monospace, SFMono-Regular, monospace; font-size: 12.5px;" id="events-container">
                    Загрузка событий...
                </div>
            </div>
        </div>

    </div>

    <!-- Toast Notification Element -->
    <div id="app-toast" class="toast"></div>

    <script>
        // Tab switching
        function switchTab(tabId) {{
            document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            const tabBtn = document.getElementById('nav-' + tabId);
            const tabContent = document.getElementById('tab-' + tabId);
            if (tabBtn) tabBtn.classList.add('active');
            if (tabContent) tabContent.classList.add('active');

            if (tabId === 'providers') fetchAuthStatus();
            if (tabId === 'ide') fetchIntegrations();
            if (tabId === 'playground') loadPlaygroundModels();
            if (tabId === 'logs') fetchEvents();
        }}

        // Toast notification
        function showToast(msg, type='info') {{
            const toast = document.getElementById('app-toast');
            toast.textContent = msg;
            toast.className = 'toast toast-' + type + ' show';
            setTimeout(() => {{
                toast.className = 'toast';
            }}, 3500);
        }}

        // Clipboard copy
        async function copyText(text, label) {{
            try {{
                if (navigator.clipboard && navigator.clipboard.writeText) {{
                    await navigator.clipboard.writeText(text);
                }} else {{
                    const ta = document.createElement('textarea');
                    ta.value = text;
                    ta.style.position = 'fixed';
                    ta.style.opacity = '0';
                    document.body.appendChild(ta);
                    ta.select();
                    document.execCommand('copy');
                    document.body.removeChild(ta);
                }}
                showToast('Скопировано: ' + (label || text), 'success');
            }} catch (err) {{
                showToast('Не удалось скопировать', 'danger');
            }}
        }}

        function copyMcpJson() {{
            const jsonText = JSON.stringify({{"zero-copy-agent": {{"url": "http://127.0.0.1:{port}/sse"}}}}, null, 2);
            copyText(jsonText, 'MCP JSON');
        }}

        // Desktop window controls
        async function minimizeToTray() {{
            try {{
                await fetch('/v1/app/minimize', {{method: 'POST'}});
            }} catch (e) {{
                console.error('Ошибка сворачивания:', e);
            }}
        }}

        async function quitApp() {{
            if (!confirm('Завершить работу zero-copy-agent?')) return;
            try {{
                await fetch('/v1/app/quit', {{method: 'POST'}});
            }} catch (e) {{
                console.error('Ошибка выхода:', e);
            }}
        }}

        // Auth & Providers
        async function fetchAuthStatus() {{
            try {{
                const res = await fetch('/v1/auth/status');
                const data = await res.json();
                if (data.providers) {{
                    for (const [prov, info] of Object.entries(data.providers)) {{
                        const badge = document.getElementById('badge-' + prov);
                        if (badge) {{
                            if (info.active) {{
                                badge.textContent = 'Активна 🟢';
                                badge.className = 'provider-status status-active';
                            }} else {{
                                badge.textContent = 'Не подключена ⚪';
                                badge.className = 'provider-status status-idle';
                            }}
                        }}
                    }}
                }}
            }} catch (e) {{}}
            fetchAccountsList();
        }}

        async function triggerBrowserLogin(provider) {{
            showToast('Запускаем браузер для входа в ' + provider.toUpperCase() + '...', 'info');
            try {{
                const res = await fetch('/v1/auth/login', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{provider}})
                }});
                const d = await res.json();
                showToast(d.message || 'Окно авторизации открыто', 'success');
            }} catch (e) {{
                showToast('Ошибка запуска: ' + e, 'danger');
            }}
            setTimeout(fetchAuthStatus, 6000);
        }}

        async function fetchAccountsList() {{
            try {{
                const res = await fetch('/v1/providers/keys');
                const data = await res.json();
                const container = document.getElementById('accounts-table-container');
                if (!data.accounts || data.accounts.length === 0) {{
                    container.innerHTML = '<div style="color: var(--text-dim); font-size: 13px; padding: 12px 0;">Нет сохраненных аккаунтов. Войдите через браузер выше или добавьте API-ключ.</div>';
                    return;
                }}
                let html = '<table class="app-table"><tr><th>Провайдер</th><th>Название</th><th>Ключ / Сессия</th><th>Статус</th><th>Действие</th></tr>';
                data.accounts.forEach(acc => {{
                    const isOk = acc.status === 'active';
                    html += `<tr>
                        <td style="font-weight: 600; color: var(--accent-blue);">${{acc.provider.toUpperCase()}}</td>
                        <td>${{acc.alias || '-'}}</td>
                        <td style="font-family: monospace; color: var(--text-dim);">${{acc.api_key_masked || 'Веб-сессия'}}</td>
                        <td style="color: ${{isOk ? 'var(--accent-emerald)' : 'var(--accent-red)'}}">${{isOk ? 'Активен 🟢' : 'Отключен 🔴'}}</td>
                        <td><button class="btn btn-danger btn-sm" onclick="deleteAccount('${{acc.id}}')">Удалить</button></td>
                    </tr>`;
                }});
                html += '</table>';
                container.innerHTML = html;
            }} catch (e) {{}}
        }}

        async function saveNewKey() {{
            const provider = document.getElementById('new-key-provider').value;
            const apiKey = document.getElementById('new-key-value').value;
            const alias = document.getElementById('new-key-alias').value;
            if (!apiKey || apiKey.trim().length < 6) {{
                showToast('Пожалуйста, введите валидный ключ', 'danger');
                return;
            }}
            try {{
                const res = await fetch('/v1/providers/keys', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{provider, api_key: apiKey, alias}})
                }});
                const d = await res.json();
                showToast(d.message || 'Ключ успешно сохранен', 'success');
                document.getElementById('new-key-value').value = '';
                document.getElementById('new-key-alias').value = '';
                fetchAuthStatus();
            }} catch (e) {{
                showToast('Ошибка сохранения: ' + e, 'danger');
            }}
        }}

        async function deleteAccount(accountId) {{
            if (!confirm('Удалить этот профиль?')) return;
            try {{
                const res = await fetch('/v1/providers/keys/' + accountId, {{method: 'DELETE'}});
                const d = await res.json();
                showToast(d.message || 'Удалено', 'info');
                fetchAuthStatus();
            }} catch (e) {{
                showToast('Ошибка: ' + e, 'danger');
            }}
        }}

        // IDE Integrations
        async function fetchIntegrations() {{
            try {{
                const res = await fetch('/v1/integrations/scan');
                const data = await res.json();
                const container = document.getElementById('ide-scan-container');
                if (data.integrations) {{
                    let html = '<table class="app-table"><tr><th>Редактор / Агент</th><th>Статус</th><th>Действия</th></tr>';
                    for (const [key, info] of Object.entries(data.integrations)) {{
                        const isConnected = info.installed && info.configured;
                        const isInstalledOnly = info.installed && !info.configured;
                        const statusColor = isConnected ? 'var(--accent-emerald)' : (isInstalledOnly ? 'var(--accent-amber)' : 'var(--text-dim)');
                        const statusText = isConnected ? 'Подключен 🟢' : (isInstalledOnly ? 'Не настроен 🟡' : 'Не найден ⚪');
                        html += `<tr>
                            <td style="font-weight: 600;">${{info.name}}</td>
                            <td style="color: ${{statusColor}};">${{statusText}}</td>
                            <td>
                                <button class="btn btn-outline btn-sm" onclick="applyIntegration('${{key}}')">Подключить</button>
                                <button class="btn btn-outline btn-sm" onclick="revertIntegration('${{key}}')">Откатить</button>
                                <button class="btn btn-outline btn-sm" onclick="testIntegration('${{key}}')">Тест</button>
                            </td>
                        </tr>`;
                    }}
                    html += '</table>';
                    container.innerHTML = html;
                }}
            }} catch (e) {{}}
        }}

        async function applyIntegration(target) {{
            try {{
                const res = await fetch('/v1/integrations/apply/' + target, {{method: 'POST'}});
                const d = await res.json();
                showToast('Настройки применены', 'success');
                fetchIntegrations();
            }} catch (e) {{
                showToast('Ошибка: ' + e, 'danger');
            }}
        }}

        async function revertIntegration(target) {{
            try {{
                const res = await fetch('/v1/integrations/revert/' + target, {{method: 'POST'}});
                const d = await res.json();
                showToast('Откат выполнен', 'info');
                fetchIntegrations();
            }} catch (e) {{
                showToast('Ошибка: ' + e, 'danger');
            }}
        }}

        async function testIntegration(target) {{
            try {{
                const res = await fetch('/v1/integrations/test/' + target);
                const d = await res.json();
                showToast(d.message || (d.connected ? 'Связь в порядке' : 'Ошибка связи'), d.connected ? 'success' : 'danger');
            }} catch (e) {{
                showToast('Ошибка: ' + e, 'danger');
            }}
        }}

        async function autoSetupAllIDEs() {{
            showToast('Настраиваем обнаруженные среды...', 'info');
            try {{
                const res = await fetch('/v1/integrations/apply/all', {{method: 'POST'}});
                const d = await res.json();
                showToast('Все среды настроены', 'success');
                fetchIntegrations();
            }} catch (e) {{
                showToast('Ошибка: ' + e, 'danger');
            }}
        }}

        // Playground
        async function loadPlaygroundModels() {{
            try {{
                const res = await fetch('/v1/models');
                const data = await res.json();
                const select = document.getElementById('play-model-select');
                if (data.data) {{
                    select.innerHTML = data.data.map(m => `<option value="${{m.id}}">${{m.id}}</option>`).join('');
                }}
            }} catch (e) {{}}
        }}

        function setPlaygroundTemplate(type) {{
            const promptEl = document.getElementById('play-prompt');
            const modelEl = document.getElementById('play-model-select');
            if (type === 'dlp') {{
                promptEl.value = 'Проверь безопасность конфигурации базы:\\nHOST = "192.168.1.150"\\nDB_PASS = "SecretPassword123!"\\nAPI_KEY = "sk-proj-998877665544332211aabbccddeeff"\\nЧто исправить?';
            }} else if (type === 'code') {{
                promptEl.value = 'Напиши компактный класс декоратора кэширования с TTL на Python.';
            }} else if (type === 'cascade') {{
                promptEl.value = 'Спроектируй надежную архитектуру сервиса уведомлений (Email + Telegram).';
                for (let i = 0; i < modelEl.options.length; i++) {{
                    if (modelEl.options[i].value === 'cascade:architect') {{
                        modelEl.selectedIndex = i;
                        break;
                    }}
                }}
            }}
        }}

        async function runPlayground() {{
            const prompt = document.getElementById('play-prompt').value;
            const model = document.getElementById('play-model-select').value;
            const btn = document.getElementById('play-submit-btn');
            const status = document.getElementById('play-status');
            const resultBox = document.getElementById('play-result-box');
            const output = document.getElementById('play-output');

            if (!prompt.trim()) {{
                showToast('Введите текст промпта', 'danger');
                return;
            }}

            btn.disabled = true;
            status.textContent = 'Обработка запроса...';
            status.style.color = 'var(--accent-amber)';

            try {{
                const res = await fetch('/v1/playground/run', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{prompt, model}})
                }});
                const d = await res.json();
                resultBox.style.display = 'block';
                output.textContent = d.content || 'Пустой ответ';
                document.getElementById('stat-duration').textContent = `Время: ${{d.duration_ms}} мс`;
                document.getElementById('stat-dlp').textContent = `DLP: ${{d.dlp_masked}} скрыто`;
                document.getElementById('stat-bpe').textContent = `BPE: ${{d.bpe_saved}}`;
                status.textContent = 'Завершено успешно';
                status.style.color = 'var(--accent-emerald)';
            }} catch (e) {{
                status.textContent = 'Ошибка запроса';
                status.style.color = 'var(--accent-red)';
                showToast('Ошибка: ' + e, 'danger');
            }} finally {{
                btn.disabled = false;
            }}
        }}

        // Live Events
        async function fetchEvents() {{
            try {{
                const res = await fetch('/v1/events?limit=12');
                const data = await res.json();
                const container = document.getElementById('events-container');
                if (data.events && data.events.length > 0) {{
                    container.innerHTML = data.events.map(e => {{
                        let badgeColor = 'var(--accent-blue)';
                        if (e.stage.includes('dlp')) badgeColor = 'var(--accent-emerald)';
                        if (e.stage.includes('cascade')) badgeColor = 'var(--accent-purple)';
                        return `<div style="margin-bottom: 8px; border-bottom: 1px solid var(--border); padding-bottom: 6px;">
                            <span style="color: var(--text-dim);">[${{e.timestamp.substr(11, 8)}}]</span>
                            <span style="color: ${{badgeColor}}; font-weight: bold;">[${{e.stage.toUpperCase()}}]</span>
                            <span style="color: var(--text-main);">${{e.message}}</span>
                            <span style="color: var(--text-dim); float: right;">${{e.model}}</span>
                        </div>`;
                    }}).join('');
                }} else {{
                    container.innerHTML = '<span style="color: var(--text-dim);">Конвейер ожидает первых запросов... Фильтры DLP и L1/L2 сжатия активны 🟢</span>';
                }}
            }} catch (e) {{}}
        }}

        // Initial setup
        fetchAuthStatus();
        setInterval(fetchEvents, 4000);
    </script>
</body>
</html>"""
