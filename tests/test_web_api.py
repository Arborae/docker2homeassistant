from conftest import PASSWORD, csrf_of, set_account, wait_for


def test_first_run_needs_the_setup_code(app):
    client = app.test_client()
    assert client.get("/").headers["Location"].endswith("/setup")
    form = {"username": "admin", "password": PASSWORD, "confirm": PASSWORD}
    assert client.post("/setup", data={**form, "code": "WRONG"}).status_code == 400
    code = app.extensions["d2ha"].setup_code
    response = client.post("/setup", data={**form, "code": code})
    assert response.status_code == 302 and response.headers["Location"].endswith("/settings")
    assert app.extensions["d2ha"].setup_code == ""
    assert client.get("/setup").status_code == 302  # only once


def test_login_and_rate_limit(app):
    set_account(app)
    client = app.test_client()
    for _ in range(5):
        assert client.post("/login", data={"username": "admin", "password": "nope"}).status_code == 401
    blocked = client.post("/login", data={"username": "admin", "password": PASSWORD})
    assert blocked.status_code == 401 and b"Too many attempts" in blocked.data


def test_pages_require_login_and_send_security_headers(app):
    set_account(app)
    client = app.test_client()
    response = client.get("/settings")
    assert response.status_code == 302 and "/login" in response.headers["Location"]
    page = client.get("/login")
    assert "script-src 'self'" in page.headers["Content-Security-Policy"]
    assert page.headers["Cache-Control"] == "no-store"
    assert client.get("/healthz").json == {"status": "ok"}


def test_logout_is_post_only(client):
    assert client.get("/logout").status_code == 405
    assert client.post("/logout").status_code == 400  # no CSRF token
    assert client.post("/logout", data={"csrf_token": csrf_of(client)}).status_code == 302
    assert client.get("/api/v1/info").status_code == 401


def test_api_requires_auth_and_csrf(app, client):
    anonymous = app.test_client()
    assert anonymous.get("/api/v1/containers").status_code == 401
    assert client.post("/api/v1/check").status_code == 403
    assert client.post("/api/v1/check", headers={"X-CSRF-Token": csrf_of(client)}).status_code == 202


def test_containers_and_info(app, client):
    app.extensions["d2ha"].checker.check()
    data = client.get("/api/v1/containers").json
    names = {c["name"]: c for c in data["containers"]}
    assert names["web"]["update"] == "available" and names["web"]["can_update"]
    assert not names["tool"]["can_update"]  # local build
    info = client.get("/api/v1/info").json
    assert info["api"] == 1 and info["counts"]["updates"] == 1 and info["counts"]["unused_images"] == 1
    notes = client.get(f"/api/v1/containers/{names['web']['key']}/notes")
    assert notes.status_code == 200 and notes.json["version"] == "1.27.3"
    logs = client.get(f"/api/v1/containers/{names['web']['key']}/logs?tail=50").json
    assert logs["lines"][1].endswith("ERROR boom")


def test_update_job_runs_and_reports_progress(app, client):
    ctx = app.extensions["d2ha"]
    ctx.jobs.start()
    ctx.checker.check()
    key = next(c["key"] for c in ctx.ops.list_containers() if c["name"] == "web")
    response = client.post(f"/api/v1/containers/{key}/update", headers={"X-CSRF-Token": csrf_of(client)})
    assert response.status_code == 202
    job_id = response.json["id"]
    assert wait_for(lambda: client.get(f"/api/v1/jobs/{job_id}").json["state"] == "done")
    assert ctx.ops.recreated == ["id-web"]


def test_bearer_token_for_home_assistant(app, client):
    from security import new_api_token

    token, stored = new_api_token()
    app.extensions["d2ha"].store.update(lambda d: d.update(api_token_hash=stored))
    ha = app.test_client()
    headers = {"Authorization": f"Bearer {token}"}
    assert ha.get("/api/v1/containers?ha=1", headers=headers).status_code == 200
    assert ha.post("/api/v1/check", headers=headers).status_code == 202  # no CSRF with a token
    assert ha.get("/api/v1/info", headers={"Authorization": "Bearer wrong"}).status_code == 401
    # Logs and preferences are for the web UI only.
    key = app.extensions["d2ha"].ops.list_containers()[0]["key"]
    assert ha.get(f"/api/v1/containers/{key}/logs", headers=headers).status_code == 401


def test_ha_visibility_toggle(app, client):
    key = app.extensions["d2ha"].ops.list_containers()[0]["key"]
    response = client.put(f"/api/v1/containers/{key}/ha", json={"ha": False}, headers={"X-CSRF-Token": csrf_of(client)})
    assert response.status_code == 200
    visible = {c["key"] for c in client.get("/api/v1/containers?ha=1").json["containers"]}
    assert key not in visible


def test_read_only_mode_blocks_actions(make_app):
    app = make_app(read_only=True)
    set_account(app)
    client = app.test_client()
    client.post("/login", data={"username": "admin", "password": PASSWORD})
    key = app.extensions["d2ha"].ops.list_containers()[0]["key"]
    headers = {"X-CSRF-Token": csrf_of(client)}
    assert client.post(f"/api/v1/containers/{key}/update", headers=headers).json["error"] == "read_only"
    assert client.post("/api/v1/images/prune", headers=headers).status_code == 403


def test_password_change_signs_out_other_sessions(app, client):
    other = app.test_client()
    other.post("/login", data={"username": "admin", "password": PASSWORD})
    assert other.get("/api/v1/info").status_code == 200
    new = "another long password"
    client.post("/settings/password", data={"csrf_token": csrf_of(client), "current": PASSWORD, "password": new, "confirm": new})
    assert client.get("/api/v1/info").status_code == 200  # this session stays signed in
    assert other.get("/api/v1/info").status_code == 401
