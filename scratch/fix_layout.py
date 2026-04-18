import re

with open('d2ha/static/css/main.css', 'r', encoding='utf-8') as f:
    css = f.read()

# BODY: flex container, altezza piena, NIENTE scroll
new_body = """body {
    margin: 0;
    font-family: 'Outfit', system-ui, -apple-system, "Segoe UI", sans-serif;
    background: var(--bg-primary);
    background-attachment: fixed;
    color: var(--text);
    display: flex;
    height: 100vh;
    overflow: hidden;
}"""
css = re.sub(r'body\s*\{[^}]*\}', new_body, css, count=1)

# SIDEBAR: elemento flex statico, niente position speciali
new_sidebar = """.app-sidebar {
    width: 250px;
    height: 100vh;
    flex-shrink: 0;
    background: var(--bg-card);
    border-right: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    padding: 18px 14px;
    z-index: 100;
    overflow-y: auto;
    transition: transform 0.3s ease;
}"""
css = re.sub(r'\.app-sidebar\s*\{[^}]*\}', new_sidebar, css, count=1)

# MAIN-WRAPPER: prende tutto lo spazio, LUI scrolla
new_wrapper = """.main-wrapper {
    flex: 1;
    min-width: 0;
    height: 100vh;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
}"""
css = re.sub(r'\.main-wrapper\s*\{[^}]*\}', new_wrapper, css, count=1)

with open('d2ha/static/css/main.css', 'w', encoding='utf-8') as f:
    f.write(css)

print('Done: body locked, only main-wrapper scrolls')
