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


def test_update_all_stops_at_the_first_failure(app, client):
    from docker_ops import UpdateError

    ctx = app.extensions["d2ha"]
    ops = ctx.ops
    for name in ("alpha", "bravo", "charlie"):
        ops.containers.append(ops._container(name, f"{name}:1", f"img-{name}"))
        ops.images[f"img-{name}"] = {"labels": {}, "repo_digests": [f"{name}@sha256:" + "3" * 64], "created": ""}
        ctx.checker.registry.digests[f"{name}:1"] = "sha256:" + "4" * 64
    plain = ops.recreate

    def recreate(container_id, progress):
        if container_id == "id-bravo":
            raise UpdateError("error.recreate_failed", "The new container did not pass the check: it restarted.")
        return plain(container_id, progress)

    ops.recreate = recreate
    ctx.jobs.start()
    ctx.checker.check()
    order = [c["name"] for c in ops.list_containers() if c["name"] in ("web", "alpha", "bravo", "charlie")]
    response = client.post("/api/v1/update-all", headers={"X-CSRF-Token": csrf_of(client)})
    assert response.status_code == 202
    job_id = response.json["id"]
    assert wait_for(lambda: client.get(f"/api/v1/jobs/{job_id}").json["state"] == "done")
    result = client.get(f"/api/v1/jobs/{job_id}").json["result"]
    stop = order.index("bravo")
    assert result["updated"] == order[:stop]
    assert result["failed"] == [{"name": "bravo", "error": "error.recreate_failed"}]
    assert result["not_tried"] == order[stop + 1:]
    assert "id-charlie" not in ops.recreated


def test_protected_containers_are_left_out_of_update_all(app, client):
    ctx = app.extensions["d2ha"]
    ops = ctx.ops
    ops.containers.append(ops._container("dns", "dns:1", "img-dns"))
    ops.images["img-dns"] = {"labels": {}, "repo_digests": ["dns@sha256:" + "3" * 64], "created": ""}
    ctx.checker.registry.digests["dns:1"] = "sha256:" + "4" * 64
    ctx.jobs.start()
    ctx.checker.check()
    key = next(c["key"] for c in ops.list_containers() if c["name"] == "dns")
    headers = {"X-CSRF-Token": csrf_of(client)}

    assert client.put(f"/api/v1/containers/{key}/protected", json={"protected": "yes"}, headers=headers).status_code == 400
    response = client.put(f"/api/v1/containers/{key}/protected", json={"protected": True}, headers=headers)
    assert response.json == {"key": key, "protected": True}
    listed = {c["name"]: c for c in client.get("/api/v1/containers").json["containers"]}
    assert listed["dns"]["protected"] and not listed["web"]["protected"]
    assert listed["dns"]["can_update"]  # still updatable on its own

    job_id = client.post("/api/v1/update-all", headers=headers).json["id"]
    assert wait_for(lambda: client.get(f"/api/v1/jobs/{job_id}").json["state"] == "done")
    result = client.get(f"/api/v1/jobs/{job_id}").json["result"]
    assert result["protected"] == ["dns"] and "dns" not in result["updated"]
    assert "id-dns" not in ops.recreated and "id-web" in ops.recreated

    # Only the protected container is left: Update all has nothing to do.
    ops.images["img-web"]["repo_digests"] = ["nginx@sha256:" + "9" * 64]
    ctx.checker.check()
    assert client.post("/api/v1/update-all", headers=headers).status_code == 409


def test_the_protected_switch_is_for_the_web_page_only(app, client):
    from security import new_api_token

    token, stored = new_api_token()
    app.extensions["d2ha"].store.update(lambda d: d.update(api_token_hash=stored))
    key = app.extensions["d2ha"].ops.list_containers()[0]["key"]
    ha = app.test_client()
    response = ha.put(f"/api/v1/containers/{key}/protected", json={"protected": True},
                      headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_logs_refused_by_the_socket_proxy_say_what_to_do(app, client):
    import docker

    ops = app.extensions["d2ha"].ops

    def refused(*args, **kwargs):
        raise docker.errors.APIError("403 Client Error: Forbidden", response=type("R", (), {"status_code": 403})())

    ops.logs = refused
    ops.log_stream = refused
    key = ops.list_containers()[0]["key"]
    for path in (f"/api/v1/containers/{key}/logs", f"/api/v1/containers/{key}/logs/stream"):
        response = client.get(path)
        assert response.status_code == 403 and response.json == {"error": "logs_forbidden"}
