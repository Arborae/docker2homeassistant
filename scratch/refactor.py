import os
import re

tpl_dir = r"C:\Users\giova\.gemini\antigravity\scratch\docker2homeassistant\d2ha\templates"
files_to_modify = [
    "home.html", "containers.html", "images.html", "volumes.html",
    "networks.html", "updates.html", "events.html", "autodiscovery.html",
    "security_settings.html"
]

header_pattern = re.compile(r'<header>.*?</header>', re.DOTALL)
notifications_pattern = re.compile(r'{%\s*include\s*[\'"]partials/notifications_styles.html[\'"]\s*%}')

new_header = """{% include 'partials/app_sidebar.html' %}
  <div class="main-wrapper">
    {% include 'partials/app_topbar.html' %}
"""

for fname in files_to_modify:
    path = os.path.join(tpl_dir, fname)
    if not os.path.exists(path):
        print(f"Skipping {fname}, does not exist")
        continue

    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Check if already modified
    if 'partials/app_sidebar.html' in content:
        print(f"Skipping {fname}, already refactored")
        continue

    # Replace <header> block
    if header_pattern.search(content):
        content = header_pattern.sub(new_header, content)
        # We also need to add '</div>' right before '</body>' to close main-wrapper
        # Find '</body>' and replace it with '</div>\n</body>'
        content = content.replace('</body>', '  </div>\n</body>')
        
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"Refactored {fname}")
    else:
        print(f"Could not find <header> in {fname}")
