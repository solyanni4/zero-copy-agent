import re
from pathlib import Path

# Broader emoji matching:
# U+1F300 to U+1FAFF (Misc Symbols and Pictographs, Emoticons, Transport, etc.)
# U+2600 to U+27BF (Misc symbols, Dingbats)
# U+2300 to U+23FF (Misc technical, e.g. watch, hourglass)
# U+2B50, U+2934, U+2935, etc.
# Mojibake sequences: ð, â

emoji_pattern = re.compile(
    r'[\U0001F000-\U0001FAFF'
    r'\u2600-\u27BF'
    r'\u2300-\u23FF'
    r'\u2B05-\u2B55'
    r']'
    r'|ð\x9f|â\x9a|â\x9c|ð'
)

files_to_check = []
for p in [Path("desktop"), Path("core"), Path("static"), Path("main.py")]:
    if p.is_file():
        files_to_check.append(p)
    elif p.is_dir():
        for sub in p.rglob("*"):
            if sub.is_file() and sub.suffix in [".py", ".js", ".html", ".css", ".md"]:
                files_to_check.append(sub)

found = []
for f in files_to_check:
    try:
        content = f.read_text(encoding="utf-8")
        lines = content.splitlines()
        for idx, line in enumerate(lines, 1):
            matches = emoji_pattern.findall(line)
            if matches:
                found.append((str(f), idx, line.strip()))
    except Exception as e:
        print(f"Error reading {f}: {e}")

print(f"Total occurrences found: {len(found)}")
for f, idx, line in found:
    print(f"{f}:{idx}: {line}")
