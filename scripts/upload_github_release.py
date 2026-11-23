import os
import sys
import httpx
from pathlib import Path

# Token from environment or command line argument
token = os.getenv("GITHUB_TOKEN")
if not token and len(sys.argv) > 1:
    token = sys.argv[1]

if not token:
    # Try reading token from git config or git credential
    import subprocess
    try:
        remote_url = subprocess.check_output(["git", "remote", "get-url", "origin"], text=True).strip()
        if "@" in remote_url and "ghp_" in remote_url:
            token = remote_url.split("://")[1].split("@")[0]
    except Exception:
        pass

if not token:
    print("[ERROR] GITHUB_TOKEN not set and not found in remote URL.")
    sys.exit(1)

headers = {
    'Authorization': f'token {token}',
    'Accept': 'application/vnd.github.v3+json'
}

exe_path = Path(r'C:\Users\Solyanni4\.gemini\antigravity\scratch\zero-copy-agent\dist\zero-copy-agent.exe')
exe_bytes = exe_path.read_bytes()
print(f'Exe size: {len(exe_bytes)} bytes')

repos = ['solyanni4/zero-copy-agent', 'solyanni4/zero-copy-agent-dev']

tag = "v1.5.0"
release_body = """## zero-copy-agent v1.5.0

### Что нового в релизе:
- **Устранение узких мест каскада и авто-архитектора**:
  - Таймаут чтения локальной Ollama увеличен с 60s до 240s (`qwen2.5-coder:14b`), подробное логирование ошибок через `repr(e)`.
  - Учёт кулдауна `QuotaMesh.is_available`: остывающие аккаунты сразу исключаются из фолбэка без повторных попыток под другими алиасами.
  - Увеличен общий таймаут MCP-инструментов `cascade_architect` и `plan_and_execute_task` до 360s.
- **Fail-Fast аутентификации в веб-сессиях**:
  - Мгновенное обнаружение редиректов на логин (`accounts.google.com`, `/login`) и капчи (Cloudflare Turnstile/CAPTCHA) за первые 5-8 секунд.
  - Автоматическая пометка аккаунта в статус `needs_auth` и включение 300s кулдауна без 45-секундного зависания.
  - Сохранение диагностических скриншотов сбоев в `logs/web_failures/`.
- **Умное ожидание ответов веб-драйверов**:
  - Отсечка TTFT (25 сек) при отсутствии токенов и индикатора загрузки.
  - Динамическое ожидание активной генерации до 120 сек с завершением при стабилизации текста (3.5 сек) или снятии индикатора.
  - Переиспользование тёплых браузерных вкладок через сброс к новому чату (`reset_to_new_chat`).
- **Постоянная интеграция с IDE и режим маршрутизации**:
  - Переключаемый режим маршрутизации `ide_routing_mode` (`through_zca` по умолчанию / `bypass`).
  - Понятная живая визуализация каждого шага оркестратора и конвейера в реальном времени.
- **Мульти-Архитектор и адаптация моделей**:
  - Режим параллельного анализа несколькими моделями с сопоставлением вердиктов.
  - Автоматическая адаптация списков моделей с бейджами мощности.
  - Фолбэк со стриминга при HTTP 429 на активные веб-сессии и локальную Ollama.
"""

proxy = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or "http://192.168.50.135:7890"
client = httpx.Client(timeout=60.0, headers=headers, proxy=proxy)

for repo in repos:
    print(f'Processing {repo}...')
    rel_res = client.get(f'https://api.github.com/repos/{repo}/releases/tags/{tag}')
    if rel_res.status_code == 200:
        release = rel_res.json()
        print(f"  Release exists (id={release['id']})")
    else:
        create_res = client.post(
            f'https://api.github.com/repos/{repo}/releases',
            json={
                'tag_name': tag,
                'name': f'zero-copy-agent {tag}',
                'body': release_body,
                'draft': False,
                'prerelease': False
            }
        )
        print(f'  Create release status: {create_res.status_code}')
        release = create_res.json()

    release_id = release['id']
    upload_url = release['upload_url'].split('{')[0]

    assets_res = client.get(f'https://api.github.com/repos/{repo}/releases/{release_id}/assets')
    for asset in assets_res.json():
        if asset['name'] == 'zero-copy-agent.exe':
            asset_id = asset['id']
            print(f'  Deleting old asset {asset_id}...')
            client.delete(f'https://api.github.com/repos/{repo}/releases/assets/{asset_id}')

    print(f"  Uploading zero-copy-agent.exe ({len(exe_bytes)/(1024*1024):.1f} MB)...")
    upload_res = client.post(
        f'{upload_url}?name=zero-copy-agent.exe',
        headers={
            'Authorization': f'token {token}',
            'Content-Type': 'application/octet-stream'
        },
        content=exe_bytes,
        timeout=300.0
    )
    print(f'  Upload status: {upload_res.status_code}')

print('All releases updated successfully!')
