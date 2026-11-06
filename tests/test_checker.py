from checker import UpdateChecker, describe


def checker_for(app):
    return app.extensions["d2ha"].checker


def views_by_name(app):
    ctx = app.extensions["d2ha"]
    return {c["name"]: ctx.checker.view(c) for c in ctx.ops.list_containers()}


def test_statuses_before_and_after_a_check(app):
    views = views_by_name(app)
    assert views["web"]["update"] == "unknown"
    assert views["tool"]["update"] == "local"  # built locally: no registry digest

    checker_for(app).check()
    views = views_by_name(app)
    web = views["web"]
    assert web["update"] == "available"
    assert web["installed_version"] == "1.27.0"
    assert web["latest_version"] == "1.27.3"
    assert web["has_notes"] and not web["breaking"]
    assert web["release_url"] == "https://github.com/nginx/nginx"
    assert views["db"]["update"] == "current"
    assert views["tool"]["update"] == "local"


def test_state_is_persisted(app, make_app):
    checker_for(app).check()
    reloaded = make_app()
    assert views_by_name(reloaded)["web"]["update"] == "available"


def test_same_version_label_is_disambiguated_by_digest(app):
    registry = app.extensions["d2ha"].checker.registry
    registry.remote_labels = lambda ref, digest, arch="amd64": {"org.opencontainers.image.version": "1.27.0"}
    checker_for(app).check()
    web = views_by_name(app)["web"]
    assert web["installed_version"] != web["latest_version"]
    assert web["installed_version"].startswith("1.27.0 (") and web["latest_version"].startswith("1.27.0 (")


def test_first_check_is_due_immediately_then_after_the_interval(app):
    checker = checker_for(app)
    assert checker.due()  # a fresh install checks right away
    checker.check()
    assert not checker.due()
    assert checker.next_check == checker.checked_at + 6 * 3600


def test_describe():
    assert describe("2.1.0", "latest", "sha256:abcdef1234") == "2.1.0"
    assert describe("", "latest", "sha256:abcdef1234") == "latest (abcdef1)"
    assert describe("", "stable", "") == "stable"


def test_registry_errors_fall_back_to_the_daemon(app):
    registry = app.extensions["d2ha"].checker.registry
    from registry import RegistryError

    def broken(ref):
        raise RegistryError("HTTP 500")

    registry.remote_digest = broken
    checker_for(app).check()
    # The fake daemon reports a digest nobody has: an update is available.
    assert views_by_name(app)["db"]["update"] == "available"
    assert isinstance(checker_for(app), UpdateChecker)
