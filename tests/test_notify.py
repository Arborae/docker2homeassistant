import requests

from conftest import FakeTelegram, csrf_of
from docker_ops import docker_proxy_host, is_proxy_container
from notify import REPEAT_SECONDS, Notifier, NotifyError, Telegram, split_message
from store import Store

SECRET = "123456:SECRET-TOKEN"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make_notifier(tmp_path, telegram=None, clock=None):
    store = Store(str(tmp_path / "d2ha.json"), 6)
    return Notifier(telegram or FakeTelegram(), store, clock or Clock()), store


def event(action, name="web", exit_code=None):
    attrs = {"name": name, "image": "nginx:1.27"}
    if exit_code is not None:
        attrs["exitCode"] = exit_code
    return {"Type": "container", "Action": action, "Actor": {"Attributes": attrs}}


def test_split_message_keeps_lines_whole():
    text = "\n".join(f"line {i} " + "x" * 50 for i in range(300))
    chunks = split_message(text, limit=1000)
    assert all(len(c) <= 1000 for c in chunks)
    assert "\n".join(chunks) == text


class FailingSession:
    def __init__(self, exc=None, status=200, body=None):
        self.exc, self.status, self.body = exc, status, body or {}

    def post(self, url, **kwargs):
        if self.exc:
            raise self.exc
        response = requests.Response()
        response.status_code = self.status
        response._content = __import__("json").dumps(self.body).encode()
        return response


def test_telegram_errors_never_contain_the_token():
    leak = requests.ConnectionError(f"HTTPSConnectionPool: /bot{SECRET}/sendMessage refused")
    for session in (FailingSession(exc=leak),
                    FailingSession(status=401, body={"description": "Unauthorized"})):
        telegram = Telegram(SECRET, ["42"], session=session)
        try:
            telegram.send("hello")
        except NotifyError as exc:
            assert SECRET not in str(exc) and "SECRET" not in repr(exc.__cause__)
        else:
            raise AssertionError("no error")
    assert not Telegram("", ["42"]).configured and not Telegram(SECRET, []).configured


def test_crash_and_unhealthy_messages(tmp_path):
    clock = Clock()
    notifier, _ = make_notifier(tmp_path, clock=clock)
    assert notifier.event_message(event("die", exit_code="0")) is None
    assert notifier.event_message(event("die", exit_code="143")) is None
    assert notifier.event_message(event("die", name="web-d2ha-old", exit_code="137")) is None
    assert notifier.event_message(event("health_status: healthy")) is None
    crash = notifier.event_message(event("die", name="<b>web</b>", exit_code="1"))
    assert "&lt;b&gt;web&lt;/b&gt;" in crash and "<b>1</b>" in crash
    assert "unhealthy" in notifier.event_message(event("health_status: unhealthy"))


def test_repeated_alerts_are_held_back(tmp_path):
    clock = Clock()
    notifier, _ = make_notifier(tmp_path, clock=clock)
    assert notifier.event_message(event("die", exit_code="1"))
    clock.now += 60
    assert notifier.event_message(event("die", exit_code="1")) is None
    assert notifier.event_message(event("die", name="db", exit_code="1"))  # another container
    clock.now += REPEAT_SECONDS
    assert notifier.event_message(event("die", exit_code="1"))


def test_switches_and_language(tmp_path):
    notifier, store = make_notifier(tmp_path)
    store.update(lambda d: d["notifications"].update(crashes=False, language="it"))
    assert notifier.event_message(event("die", exit_code="1")) is None
    assert "non è in salute" in notifier.event_message(event("health_status: unhealthy"))
    off, _ = make_notifier(tmp_path / "off", telegram=FakeTelegram(configured=False))
    assert off.event_message(event("die", exit_code="1")) is None
    assert off.updates([{"names": ["web"], "installed": "1", "latest": "2"}]) is False


def test_each_new_version_is_announced_once(app):
    d2ha = app.extensions["d2ha"]
    telegram = FakeTelegram()
    d2ha.notifier.telegram = telegram
    d2ha.checker.check()
    assert len(telegram.sent) == 1
    message = telegram.sent[0]
    assert "<b>web</b> (site)" in message and "1.27.3" in message and "https://github.com/nginx/nginx" in message
    d2ha.checker.check()
    assert len(telegram.sent) == 1  # same digest: already announced
    d2ha.checker.registry.digests["nginx:1.27"] = "sha256:" + "8" * 64
    d2ha.checker.check()
    assert len(telegram.sent) == 2  # a newer version


def test_failed_announcements_are_retried(app):
    d2ha = app.extensions["d2ha"]
    d2ha.notifier.telegram = FakeTelegram(error=NotifyError("down"))
    d2ha.checker.check()
    assert d2ha.checker.notified == {}
    working = FakeTelegram()
    d2ha.notifier.telegram = working
    d2ha.checker.check()
    assert len(working.sent) == 1


def test_proxy_host_and_container():
    assert docker_proxy_host("tcp://docker-proxy:2375") == "docker-proxy"
    assert docker_proxy_host("unix:///var/run/docker.sock") == ""
    assert docker_proxy_host("tcp://127.0.0.1:2375") == ""
    assert docker_proxy_host("tcp://localhost:2375") == ""
    assert docker_proxy_host("") == ""
    assert is_proxy_container("docker-proxy", "d2ha-docker-proxy", {"com.docker.compose.service": "docker-proxy"}, {})
    assert is_proxy_container("proxy", "x", {}, {"net": {"Aliases": ["proxy"], "DNSNames": None}})
    assert not is_proxy_container("docker-proxy", "web", {}, {"net": {"Aliases": None}})
    assert not is_proxy_container("", "docker-proxy", {}, {})


def test_the_proxy_is_never_updated(app, client):
    d2ha = app.extensions["d2ha"]
    d2ha.ops.containers[0]["is_proxy"] = True  # "web", which has an update
    d2ha.checker.check()
    web = {c["name"]: c for c in client.get("/api/v1/containers").json["containers"]}["web"]
    assert web["update"] == "available" and not web["can_update"]
    headers = {"X-CSRF-Token": csrf_of(client)}
    response = client.post(f"/api/v1/containers/{web['key']}/update", headers=headers)
    assert response.status_code == 400 and response.json["error"] == "proxy_update"
    assert client.post("/api/v1/update-all", headers=headers).status_code == 409


def test_notification_settings_and_test_message(app, client):
    d2ha = app.extensions["d2ha"]
    page = client.get("/settings").data.decode()
    assert "D2HA_TELEGRAM_TOKEN" in page  # not configured: explains how
    form = {"csrf_token": csrf_of(client)}
    client.post("/settings/notifications/test", data=form)
    assert "Telegram is not configured" in client.get("/settings").data.decode()

    telegram = FakeTelegram()
    d2ha.notifier.telegram = telegram
    assert 'name="unhealthy"' in client.get("/settings").data.decode()
    client.post("/settings/notifications", data={**form, "updates": "on", "language": "it"})
    saved = d2ha.store.get("notifications")
    assert saved == {"updates": True, "crashes": False, "unhealthy": False, "language": "it"}
    client.post("/settings/notifications/test", data=form)
    assert telegram.sent and "funzionano" in telegram.sent[0]

    d2ha.notifier.telegram = FakeTelegram(error=NotifyError("Telegram error 400 Bad Request: chat not found"))
    client.post("/settings/notifications/test", data=form)
    assert "chat not found" in client.get("/settings").data.decode()


def test_the_events_watcher_resumes_without_losing_events(tmp_path, monkeypatch):
    telegram = FakeTelegram()
    notifier, _ = make_notifier(tmp_path, telegram=telegram)
    sleeps, calls = [], []
    notifier.sleep = sleeps.append
    times = iter([0, 600, 600, 600, 601, 601, 700, 700])  # opened, closed (idle), ...
    monkeypatch.setattr("notify.time.time", lambda: next(times))

    def source(since):
        calls.append(since)
        if len(calls) == 1:
            return iter([event("die", exit_code="1")])  # then the proxy closes the idle stream
        if len(calls) == 2:
            raise ConnectionError("proxy restarting")
        return iter([event("die", name="db", exit_code="2")])

    notifier.watch(source, rounds=3)
    assert calls == [None, 599, 600]  # resumed from just before each cut
    assert sleeps == [5]  # waited only after the error
    assert len(telegram.sent) == 2
