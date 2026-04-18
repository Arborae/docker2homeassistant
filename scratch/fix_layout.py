import re

with open('d2ha/static/css/main.css', 'r', encoding='utf-8') as f:
    css = f.read()

# FIX body: ripristina display:flex
new_body = """body {
    margin: 0;
    font-family: 'Outfit', system-ui, -apple-system, "Segoe UI", sans-serif;
    background: var(--bg-primary);
    background-attachment: fixed;
    color: var(--text);
    display: flex;
    min-height: 100vh;
}"""
css = re.sub(r'body\s*\{[^}]*\}', new_body, css, count=1)

# FIX sidebar: usa position:sticky invece di fixed
new_sidebar = """.app-sidebar {
    width: 250px;
    height: 100vh;
    position: sticky;
    top: 0;
    align-self: flex-start;
    flex-shrink: 0;
    background: var(--bg-card);
    border-right: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    padding: 18px 14px;
    z-index: 100;
    backdrop-filter: var(--blur-strong);
    -webkit-backdrop-filter: var(--blur-strong);
    transition: transform 0.3s ease;
}"""
css = re.sub(r'\.app-sidebar\s*\{[^}]*\}', new_sidebar, css, count=1)

# FIX main-wrapper: niente margin-left, usa flex:1
new_wrapper = """.main-wrapper {
    flex: 1;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    min-width: 0;
}"""
css = re.sub(r'\.main-wrapper\s*\{[^}]*\}', new_wrapper, css, count=1)

with open('d2ha/static/css/main.css', 'w', encoding='utf-8') as f:
    f.write(css)

print('Applied sticky sidebar approach')
