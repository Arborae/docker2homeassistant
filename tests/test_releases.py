from releases import ReleaseNotes, extract_breaking, github_repo


def test_github_repo_from_labels_and_ghcr():
    assert github_repo({"org.opencontainers.image.source": "https://github.com/home-assistant/core"}, "x") == ("home-assistant", "core")
    assert github_repo({"org.label-schema.vcs-url": "git@github.com:owner/repo.git"}, "x") == ("owner", "repo")
    assert github_repo({}, "ghcr.io/owner/app:1.0") == ("owner", "app")
    assert github_repo({}, "nginx:latest") is None  # no guessing for Docker Hub


def test_extract_breaking_section():
    body = "## Features\n- a\n## Breaking changes\n- removed X\n### Details\nmore\n## Fixes\n- b"
    assert extract_breaking(body) == "- removed X\n### Details\nmore"
    assert extract_breaking("## Backward-incompatible changes\n- y") == "- y"
    assert extract_breaking("nothing special") == ""


class FakeResponse:
    status_code = 200

    def json(self):
        return [
            {"tag_name": "v2.0.0", "name": "2.0.0", "body": "## Breaking changes\n- big", "html_url": "u2", "published_at": "2026-02-01T00:00:00Z"},
            {"tag_name": "v1.9.0", "name": "1.9.0", "body": "fixes", "html_url": "u1", "published_at": "2026-01-01T00:00:00Z"},
            {"tag_name": "v3.0.0", "draft": True},
        ]


class FakeSession:
    headers = {}

    def get(self, url, params=None, timeout=None):
        return FakeResponse()


def test_find_matches_the_exact_version_only():
    notes = ReleaseNotes()
    notes.session = FakeSession()
    found = notes.find("o", "r", "2.0.0")
    assert found["url"] == "u2" and found["breaking"] == "- big" and found["published"] == "2026-02-01"
    assert notes.find("o", "r", "v1.9.0")["url"] == "u1"
    assert notes.find("o", "r", "3.0.0") is None  # drafts are ignored
    assert notes.find("o", "r", "") is None
