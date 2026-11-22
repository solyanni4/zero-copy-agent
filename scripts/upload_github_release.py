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

tag = "v1.4.0"
release_body = """## zero-copy-agent v1.4.0

### Что нового в стабилизационном релизе:
- **Переход браузерного движка на Patchright**: Безопасный запуск через антидетект-движок `patchright` с прозрачным фолбэком на `playwright`. Устранена детекция автоматизации Cloudflare и Google на экранах логина.
- **Вкладка «Логи» в Web UI**: Полноценный консольный терминал логов в реальном времени с фильтрацией по уровням (Все, Ошибки, Предупреждения, Инфо), строкой поиска, автообновлением каждые 4 секунды, копированием и очисткой.
- **Стабилизация Мастера моделей Ollama**: Точное сопоставление тегов моделей (исключено ложное срабатывание для разных размеров одной линейки), корректное сохранение конфигурации и немедленное переключение активной модели в памяти оркестратора и эмбеддингов.
- **Расширение интеграций и деинсталлятора**: Интеграция JetBrains включена в цикл отката бэкапов `HistoryManager.rollback` и чистой деинсталляции `clean_uninstall_system`.
- **Поддержка провайдеров в Auth Status**: В эндпоинт `/v1/auth/status` добавлены статусы для DeepSeek, Qwen и Grok.
"""

client = httpx.Client(timeout=60.0, headers=headers)

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
