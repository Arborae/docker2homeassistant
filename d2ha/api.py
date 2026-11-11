"""JSON API v1, used by the web UI (session + CSRF) and by Home Assistant (bearer token)."""

import json
import queue
import threading
import time
from typing import Any, Dict, Iterator, List

from flask import Blueprint, Response, current_app, jsonify, request, stream_with_context

from docker_ops import UpdateError
from security import api_auth, client_ip

api_bp = Blueprint("api", __name__, url_prefix="/api/v1")
API_VERSION = 1
LIVE_LOG_MAX_SECONDS = 30 * 60


def ctx():
    return current_app.extensions["d2ha"]


def _views() -> List[Dict[str, Any]]:
    read_only = ctx().config.read_only
    views = []
    for container in ctx().ops.list_containers():
        view = ctx().checker.view(container)
        view["can_update"] = (not read_only and not view["is_self"] and not view["is_proxy"]
                              and view["update"] not in ("pinned", "local"))
        views.append(view)
    return views


def _find(key: str):
    container = ctx().find_container(key)
    if not container:
        return None, (jsonify(error="not_found"), 404)
    return container, None


def _write_guard():
    """Common checks for actions: read-only mode and rate limit."""
    if ctx().config.read_only:
        return jsonify(error="read_only"), 403
    if not ctx().write_limiter.allow(client_ip()):
        return jsonify(error="rate_limited"), 429
    return None


def _sse(events: Iterator[str]) -> Response:
    return Response(stream_with_context(events), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# -- read --------------------------------------------------------------------------

@api_bp.route("/info")
@api_auth()
def info():
    checker = ctx().checker
    views = _views()
    try:
        unused = len(ctx().ops.unused_images())
    except Exception:  # noqa: BLE001 - counts are informative only
        unused = None
    return jsonify(
        version=ctx().config.version,
        api=API_VERSION,
        read_only=ctx().config.read_only,
        docker=ctx().ops.engine_info().get("version", ""),
        check={
            "last": checker.checked_at or None,
            "next": checker.next_check or None,
            "interval_hours": checker.interval_seconds // 3600,
            "running": checker.running,
        },
        counts={
            "containers": len(views),
            "running": sum(1 for v in views if v["state"] == "running"),
            "updates": sum(1 for v in views if v["update"] == "available"),
            "unused_images": unused,
        },
    )


@api_bp.route("/containers")
@api_auth()
def containers():
    views = _views()
    if request.args.get("ha") == "1":
        views = [v for v in views if v["ha"] and not v["is_self"]]
    return jsonify(containers=views, checked_at=ctx().checker.checked_at or None,
                   checking=ctx().checker.running)


@api_bp.route("/containers/<key>/notes")
@api_auth()
def notes(key: str):
    container, error = _find(key)
    if error:
        return error
    data = ctx().checker.notes_for(container["image"])
    if not data:
        return jsonify(error="no_notes"), 404
    return jsonify(data)


@api_bp.route("/containers/<key>/logs")
@api_auth(allow_token=False)
def logs(key: str):
    container, error = _find(key)
    if error:
        return error
    try:
        tail = max(10, min(int(request.args.get("tail", 300)), 5000))
    except ValueError:
        tail = 300
    text = ctx().ops.logs(container["id"], tail=tail)
    return jsonify(lines=text.splitlines(), tail=tail)


@api_bp.route("/containers/<key>/logs/stream")
@api_auth(allow_token=False)
def logs_stream(key: str):
    container, error = _find(key)
    if error:
        return error
    stream = ctx().ops.log_stream(container["id"])
    lines: "queue.Queue[str]" = queue.Queue(maxsize=2000)

    def reader():
        buffer = ""
        try:
            for chunk in stream:
                buffer += chunk.decode("utf-8", errors="replace")
                *complete, buffer = buffer.split("\n")
                for line in complete:
                    try:
                        lines.put_nowait(line)
                    except queue.Full:
                        pass
        except Exception:  # noqa: BLE001 - the stream ends when it is closed
            pass

    threading.Thread(target=reader, name="log-follow", daemon=True).start()

    def events():
        started = time.time()
        try:
            while time.time() - started < LIVE_LOG_MAX_SECONDS:
                batch: List[str] = []
                try:
                    batch.append(lines.get(timeout=15))
                    while len(batch) < 200:
                        batch.append(lines.get_nowait())
                except queue.Empty:
                    pass
                # A ping also detects a closed browser, so the Docker stream is released.
                yield f"data: {json.dumps(batch)}\n\n" if batch else ": ping\n\n"
        finally:
            stream.close()

    return _sse(events())


@api_bp.route("/jobs")
@api_auth()
def active_jobs():
    return jsonify(jobs=[job.as_dict() for job in ctx().jobs.active()])


@api_bp.route("/jobs/<job_id>")
@api_auth()
def job_status(job_id: str):
    job = ctx().jobs.get(job_id)
    if not job:
        return jsonify(error="not_found"), 404
    return jsonify(job.as_dict())


@api_bp.route("/jobs/<job_id>/events")
@api_auth()
def job_events(job_id: str):
    job = ctx().jobs.get(job_id)
    if not job:
        return jsonify(error="not_found"), 404
    updates = job.subscribe()

    def events():
        try:
            while True:
                try:
                    snapshot = updates.get(timeout=15)
                except queue.Empty:
                    yield ": ping\n\n"
                    continue
                yield f"data: {json.dumps(snapshot)}\n\n"
                if snapshot["state"] in ("done", "failed"):
                    return
        finally:
            job.unsubscribe(updates)

    return _sse(events())


# -- actions -------------------------------------------------------------------------

def _progress(job):
    def report(event: Dict[str, Any]) -> None:
        job.publish(**{k: event[k] for k in ("phase", "percent", "current", "total", "detail") if k in event})

    return report


def _update_one(app_ctx, key: str, job) -> Dict[str, Any]:
    # Looked up when the job runs: another job may have recreated it meanwhile.
    container = app_ctx.find_container(key)
    if not container:
        raise UpdateError("error.not_found", key)
    result = app_ctx.ops.recreate(container["id"], _progress(job))
    app_ctx.checker.check(only_ref=container["image"])
    return result


@api_bp.route("/check", methods=["POST"])
@api_auth()
def check_now():
    if not ctx().write_limiter.allow(client_ip()):
        return jsonify(error="rate_limited"), 429
    ctx().checker.trigger()
    return jsonify(started=True), 202


@api_bp.route("/containers/<key>/update", methods=["POST"])
@api_auth()
def update(key: str):
    guard = _write_guard()
    if guard:
        return guard
    container, error = _find(key)
    if error:
        return error
    if container["is_self"]:
        return jsonify(error="self_update"), 400
    if container["is_proxy"]:
        return jsonify(error="proxy_update"), 400
    app_ctx = ctx()
    job = app_ctx.jobs.submit("update", key, container["name"],
                              lambda j: _update_one(app_ctx, key, j))
    return jsonify(job.as_dict()), 202


@api_bp.route("/update-all", methods=["POST"])
@api_auth()
def update_all():
    guard = _write_guard()
    if guard:
        return guard
    targets = [v for v in _views() if v["update"] == "available" and v["can_update"]]
    if not targets:
        return jsonify(error="nothing_to_update"), 409
    app_ctx = ctx()

    def work(job):
        updated, failed = [], []
        job.publish(steps=len(targets))
        for index, container in enumerate(targets, start=1):
            job.publish(step=index, label=container["name"], phase="pull", percent=0, current=0, total=0)
            try:
                _update_one(app_ctx, container["key"], job)
                updated.append(container["name"])
            except Exception as exc:  # noqa: BLE001 - reported in the result
                failed.append({"name": container["name"], "error": getattr(exc, "code", "error.generic")})
                break  # something is wrong: the others wait for a look
        not_tried = [c["name"] for c in targets[len(updated) + len(failed):]]
        if failed and not updated:
            raise UpdateError("error.update_all_failed",
                              f"{failed[0]['name']}; not tried: {', '.join(not_tried) or '-'}")
        return {"updated": updated, "failed": failed, "not_tried": not_tried}

    job = app_ctx.jobs.submit("update_all", "", "", work)
    return jsonify(job.as_dict()), 202


@api_bp.route("/images/prune", methods=["POST"])
@api_auth()
def prune():
    guard = _write_guard()
    if guard:
        return guard
    app_ctx = ctx()
    job = app_ctx.jobs.submit("prune", "", "", lambda j: app_ctx.ops.prune_images())
    return jsonify(job.as_dict()), 202


@api_bp.route("/containers/<key>/ha", methods=["PUT"])
@api_auth(allow_token=False)
def set_ha_visibility(key: str):
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload.get("ha"), bool):
        return jsonify(error="bad_request"), 400
    ctx().store.set_container_in_ha(key, payload["ha"])
    return jsonify(key=key, ha=payload["ha"])
