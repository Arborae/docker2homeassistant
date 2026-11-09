"""The documentation pages in docs/ are plain HTML: these checks keep them consistent."""
import os
import posixpath
import re
from html.parser import HTMLParser

DOCS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs")


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self.ids, self.current = [], [], []
        self.title = self.description = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        for name in ("href", "src"):
            if attrs.get(name):
                self.links.append(attrs[name])
        if attrs.get("id"):
            self.ids.append(attrs["id"])
        if attrs.get("aria-current") == "page":
            self.current.append(attrs.get("href"))
        if tag == "meta" and attrs.get("name") == "description":
            self.description = attrs.get("content", "")
        self._in_title = tag == "title"

    def handle_data(self, data):
        if self._in_title:
            self.title += data

    def handle_endtag(self, tag):
        self._in_title = False


def doc_pages():
    """Published path ("guide/logs/") of every documentation page."""
    found = ["install/"]
    for folder, _, files in os.walk(os.path.join(DOCS, "guide")):
        if "index.html" in files:
            found.append(posixpath.relpath(folder, DOCS).replace(os.sep, "/") + "/")
    return sorted(found)


def location(path):
    """The file behind a published path: a folder ("guide/", "" for the home page) means its index.html."""
    return os.path.join(DOCS, path, "index.html") if path == "" or path.endswith("/") else os.path.join(DOCS, path)


def read(path):
    with open(location(path), encoding="utf-8") as fh:
        return fh.read()


def parse(text):
    page = Page()
    page.feed(text)
    return page


def resolve(base, href):
    """A relative link found in the page ``base``, as a published path (without the anchor)."""
    target = href.split("#", 1)[0]
    if not target:
        return base
    joined = posixpath.normpath(posixpath.join(base or ".", target))
    if joined == ".":
        return ""
    return joined + "/" if target.endswith("/") else joined


def section(text, start, end):
    match = re.search(start + r".*?" + end, text, re.S)
    assert match, start
    return match.group(0)


def sidebar(path):
    block = section(read(path), r'<aside class="docs-sidebar"', r"</aside>")
    return [resolve(path, href) for href in re.findall(r'href="([^"]+)"', block)]


def test_every_page_has_the_same_sidebar_listing_every_page():
    pages = doc_pages()
    first = sidebar(pages[0])
    assert sorted(first) == pages
    for path in pages:
        assert sidebar(path) == first, path


def test_the_current_page_is_marked_in_the_sidebar():
    for path in doc_pages():
        block = section(read(path), r'<aside class="docs-sidebar"', r"</aside>")
        current = re.findall(r'href="([^"]+)" aria-current="page"', block)
        assert [resolve(path, href) for href in current] == [path], path


def test_previous_and_next_follow_the_sidebar():
    order = sidebar(doc_pages()[0])
    for i, path in enumerate(order):
        block = section(read(path), r'<nav class="pager"', r"</nav>")
        prev = re.findall(r'class="prev" href="([^"]+)"', block)
        nxt = re.findall(r'class="next" href="([^"]+)"', block)
        assert [resolve(path, h) for h in prev] == order[max(i - 1, 0):i], path
        assert [resolve(path, h) for h in nxt] == order[i + 1:i + 2], path


def test_local_links_and_images_exist():
    for path in [""] + doc_pages():
        page = parse(read(path))
        for href in page.links:
            if re.match(r"^(https?:|mailto:)", href):
                continue
            target = resolve(path, href)
            assert os.path.isfile(location(target)), f"{path or 'index'}: {href}"
            if "#" in href and href.split("#", 1)[1]:
                anchor = href.split("#", 1)[1]
                assert anchor in parse(read(target)).ids, f"{path or 'index'}: {href}"


def test_pages_have_a_title_a_description_and_unique_ids():
    for path in doc_pages():
        page = parse(read(path))
        assert page.title.strip().endswith("D2HA"), path
        assert page.description, path
        assert len(page.ids) == len(set(page.ids)), path
