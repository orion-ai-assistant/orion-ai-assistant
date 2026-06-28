import re
from pathlib import Path

root = Path(__file__).parent
pattern = re.compile(r'(logger\.(?:debug|info|warning|error|stream)\()\s*f(["\'])')
repl = r'\1lambda: f\2'
changed = []
for path in root.rglob('*.py'):
    if 'site-packages' in path.parts or '.venv' in path.parts:
        continue
    text = path.read_text(encoding='utf-8')
    new_text, count = pattern.subn(repl, text)
    if count > 0:
        path.write_text(new_text, encoding='utf-8')
        changed.append((path, count))

print('changed files:', len(changed))
for p, count in changed:
    print(p, count)
