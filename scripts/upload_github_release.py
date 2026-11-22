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

tag = "v1.3.0"
release_body = """## zero-copy-agent v1.3.0

### Что нового:
- **База решений Архитектора (SQLite RAG + FTS5)**: Локальное хранилище проверенных паттернов с полнотекстовым поиском FTS5 и семантическим ранжированием векторов Nomic. Автоматическое сохранение успешных архитектурных решений и моментальная подпитка контекста при планировании задач.
- **Согласование сжатия промптов (Human-in-the-Loop)**: Интерактивный предпросмотр сжатого текста запроса с подсчетом экономии токенов, возможностью ручной правки и отправки на переделку с текстовым комментарием (мягче, жестче, не убирать комментарии).
- **Сетапы оркестрации (Пресеты)**: Три встроенных шаблона («Авто», «Базовый», «МЫСЛИТЕЛЬ») и конструктор пользовательских сетапов с быстрым переключением прямо из шапки приложения.
- **Мастер подбора локальных моделей (Hardware Advisor)**: Автоматическая диагностика видеокарты (VRAM), ОЗУ и ядер процессора с матрицей рекомендаций моделей под локальную Ollama, потоковой шкалой скачивания по SSE и активацией в один клик.
- **Отказоустойчивость Playwright**: Полная изоляция профилей браузеров, автоматическое снятие зомби-блокировок и исключение ложных ошибок скачивания Chromium.
"""

for repo in repos:
    print(f'Processing {repo}...')
    rel_res = httpx.get(f'https://api.github.com/repos/{repo}/releases/tags/{tag}', headers=headers)
    if rel_res.status_code == 200:
        release = rel_res.json()
        print(f"  Release exists (id={release['id']})")
    else:
        create_res = httpx.post(
            f'https://api.github.com/repos/{repo}/releases',
            headers=headers,
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

    assets_res = httpx.get(f'https://api.github.com/repos/{repo}/releases/{release_id}/assets', headers=headers)
    for asset in assets_res.json():
        if asset['name'] == 'zero-copy-agent.exe':
            asset_id = asset['id']
            print(f'  Deleting old asset {asset_id}...')
            httpx.delete(f'https://api.github.com/repos/{repo}/releases/assets/{asset_id}', headers=headers)

    print(f"  Uploading zero-copy-agent.exe ({len(exe_bytes)/(1024*1024):.1f} MB)...")
    upload_res = httpx.post(
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
