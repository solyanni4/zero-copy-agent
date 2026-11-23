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

exe_path = Path(__file__).resolve().parent.parent / "dist" / "zero-copy-agent.exe"
if not exe_path.exists():
    print(f"[ERROR] Executable not found at {exe_path}. Build it first.")
    sys.exit(1)
exe_bytes = exe_path.read_bytes()
print(f'Exe size: {len(exe_bytes)} bytes')

repos = ['solyanni4/zero-copy-agent', 'solyanni4/zero-copy-agent-dev']

tag = "v1.6.0"
release_body = """## zero-copy-agent v1.6.0

### Что нового:
- **Целевой Diff-аудит (±25 строк контекста)**:
  - Проверка запускается только по списку реально изменённых файлов (`git diff -U25 -- [файлы]`).
  - Нет лишней траты токенов на перечитывание всего проекта целиком.
  - При обнаружении недочётов диффы автоматически отправляются на локальную доработку (`qwen2.5-coder:14b`).
- **Расширенный локальный Hard Gate**:
  - Быстрая проверка синтаксиса JavaScript/MJS (`node --check`) без лишних зависимостей.
  - Проверка парности скобок в PowerShell-скриптах (`.ps1`).
  - Строгий аудит: отказ от автопропуска при невалидном ответе модели — вердикт только через чёткий JSON.
- **Стабильные веб-сессии и авторизация**:
  - Фоновый keepalive для `claude.ai` наряду с остальными сервисами.
  - Неблокирующий цикл в мастере авторизации: автоматическое сохранение сессии при появлении поля ввода или закрытии окна (до 300с ожидания).
  - Устранена блокировка потоков при параллельной проверке профилей.
- **Честная экономика токенов**:
  - Подсчёт 100% экономии для локальной GPU (Ollama) и веб-сессий (бесплатная генерация).
  - Для платных API-ключей экономия рассчитывается строго по сжатым токенам промпта.
  - Автоматическая очистка старых черновиков сжатия (> 30 минут).
- **Чистка каталога моделей и интеграция с IDE**:
  - Исключение эмбеддинг-моделей (`*embed*`) из списка генераторов кода.
  - Автоматическое напоминание в правилах IDE о запуске фонового сервера через `Start-Process --headless`.
"""

proxy = os.getenv("ZCA_PROXY") or os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or None
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
