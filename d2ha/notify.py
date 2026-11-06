"""Telegram notifications: new updates, containers stopped with an error, unhealthy containers."""

import html
import logging
import threading
import time
from typing import Any, Callable, Dict, Iterable, List, Optional

import requests

import i18n
from docker_ops import OLD_SUFFIX

log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"
MAX_MESSAGE = 4096
# A normal stop exits with 0, or 143 after SIGTERM: not worth a message.
NORMAL_EXIT_CODES = {"0", "143"}
# Signals of docker stop / kill / rm -f (INT, QUIT, KILL, TERM). A container that dies within
# STOP_SECONDS of one of them was stopped on purpose; other signals (HUP: reload) are not stops.
STOP_SIGNALS = {"2", "3", "9", "15"}
STOP_SECONDS = 10 * 60
OOM_SECONDS = 60
# A container label that turns off crash and unhealthy alerts for that container.
OPT_OUT_LABEL = "d2ha.notify"
# The same alert for the same container at most this often (a crash loop would flood the chat).
REPEAT_SECONDS = 15 * 60
KINDS = ("updates", "crashes", "unhealthy")


class NotifyError(RuntimeError):
    """Sending failed. The message never contains the bot token."""


def esc(text: Any) -> str:
    return html.escape(str(text), quote=True)


def split_message(text: str, limit: int = MAX_MESSAGE) -> List[str]:
    """Split at line breaks into pieces Telegram accepts (lines keep their HTML whole)."""
    chunks, current = [], ""
    for line in text.split("\n"):
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit and current:
            chunks.append(current)
            candidate = line
        current = candidate[:limit]
    if current:
        chunks.append(current)
    return chunks


class Telegram:
    def __init__(self, token: str, chat_ids: Iterable[str], session: Optional[requests.Session] = None):
        self.token = token
        self.chat_ids = tuple(chat_ids)
        self.session = session or requests.Session()

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_ids)

    def send(self, text: str) -> None:
        for chunk in split_message(text):
            for chat_id in self.chat_ids:
                self._send_one(chat_id, chunk)

    def _send_one(self, chat_id: str, text: str) -> None:
        try:
            resp = self.session.post(
                f"{TELEGRAM_API}/bot{self.token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                      "disable_web_page_preview": True},
                timeout=15,
            )
        except requests.RequestException as exc:
            # The exception text holds the URL, hence the token: report only its type.
            raise NotifyError(f"Telegram unreachable ({type(exc).__name__})") from None
        if resp.status_code != 200:
            try:
                description = str(resp.json().get("description", ""))
            except ValueError:
                description = ""
            raise NotifyError(f"Telegram error {resp.status_code} {description}".strip())


class Notifier:
    """Decides what is worth a message and writes it in the chosen language."""

    def __init__(self, telegram: Telegram, store, clock: Callable[[], float] = time.time):
        self.telegram = telegram
        self.store = store
        self.clock = clock
        self.sleep = time.sleep
        self._last: Dict[tuple, float] = {}
        self._stopping: Dict[str, float] = {}  # container name -> time of a stop signal
        self._oom: Dict[str, float] = {}  # container name -> time of an out-of-memory kill

    @property
    def configured(self) -> bool:
        return self.telegram.configured

    def enabled(self, kind: str) -> bool:
        return self.configured and bool(self.store.get("notifications", kind, default=True))

    def _t(self, key: str, **params) -> str:
        lang = self.store.get("notifications", "language", default="") or i18n.DEFAULT_LANGUAGE
        return i18n.translate(lang, key, **params)

    # -- messages -------------------------------------------------------------------

    def test(self) -> None:
        self.telegram.send(f"✅ {esc(self._t('notify.test'))}")

    def updates(self, items: List[Dict[str, Any]]) -> bool:
        """Report new updates. Returns False when update messages are off (nothing sent)."""
        if not items or not self.enabled("updates"):
            return False
        lines = [f"🆕 <b>{esc(self._t('notify.updates_title', n=len(items)))}</b>"]
        for item in items:
            stack = f" ({esc(item['stack'])})" if item.get("stack") else ""
            lines.append(f"\n• <b>{esc(', '.join(item['names']))}</b>{stack}\n"
                         f"  {esc(item['installed'])} → <b>{esc(item['latest'])}</b>")
            if item.get("breaking"):
                lines.append(f"  ⚠️ {esc(self._t('notify.breaking'))}")
            if item.get("url"):
                lines.append(f"  <a href=\"{esc(item['url'])}\">{esc(self._t('notify.release_notes'))}</a>")
        self.telegram.send("\n".join(lines))
        log.info("Telegram: sent %d new updates", len(items))
        return True

    def event_message(self, event: Dict[str, Any]) -> Optional[str]:
        """The message for a Docker container event, or None when it is not worth one."""
        attrs = (event.get("Actor") or {}).get("Attributes") or {}
        name = str(attrs.get("name", ""))
        image = str(attrs.get("image", ""))
        action = str(event.get("Action") or event.get("status") or "")
        if not name or name.endswith(OLD_SUFFIX):
            return None  # the previous container of an update by D2HA, stopped on purpose
        now = self.clock()
        if action == "kill":
            if str(attrs.get("signal", "")) in STOP_SIGNALS:
                self._stopping[name] = now
            return None
        if action == "oom":
            self._oom[name] = now
            return None
        if str(attrs.get(OPT_OUT_LABEL, "")).strip().lower() in ("false", "0", "no", "off"):
            return None
        if action == "die":
            code = str(attrs.get("exitCode", ""))
            stopped = now - self._stopping.pop(name, float("-inf")) < STOP_SECONDS
            oom = now - self._oom.pop(name, float("-inf")) < OOM_SECONDS
            if code in NORMAL_EXIT_CODES or (stopped and not oom):
                return None
            kind = "crashes"
            text = (f"🚨 <b>{esc(name)}</b> {esc(self._t('notify.crashed'))}\n"
                    + (f"{esc(self._t('notify.oom'))}\n" if oom else "")
                    + f"{esc(self._t('notify.image'))}: <code>{esc(image)}</code>\n"
                    f"{esc(self._t('notify.exit_code'))}: <b>{esc(code or '?')}</b>")
        elif action.startswith("health_status") and action.split(":", 1)[-1].strip() == "unhealthy":
            kind = "unhealthy"
            text = (f"⚠️ <b>{esc(name)}</b> {esc(self._t('notify.unhealthy'))}\n"
                    f"{esc(self._t('notify.image'))}: <code>{esc(image)}</code>")
        else:
            return None
        if not self.enabled(kind):
            return None
        if now - self._last.get((kind, name), 0.0) < REPEAT_SECONDS:
            return None
        self._last[(kind, name)] = now
        return text

    def handle_event(self, event: Dict[str, Any]) -> None:
        text = self.event_message(event)
        if not text:
            return
        name = (event.get("Actor") or {}).get("Attributes", {}).get("name")
        try:
            self.telegram.send(text)
            log.info("Telegram: sent %s alert for %s", event.get("Action"), name)
        except NotifyError as exc:
            log.warning("Telegram: %s alert for %s not sent: %s", event.get("Action"), name, exc)

    # -- Docker events ----------------------------------------------------------------

    def start(self, events_source: Callable[[Optional[int]], Iterable[Dict[str, Any]]]) -> None:
        threading.Thread(target=self.watch, args=(events_source,), name="docker-events", daemon=True).start()

    def watch(self, events_source, rounds: Optional[int] = None) -> None:
        """Follow Docker events forever (``rounds`` limits the reconnections, for tests)."""
        delay, since = 5, None
        while rounds is None or rounds > 0:
            rounds = None if rounds is None else rounds - 1
            opened = self.clock()
            try:
                for event in events_source(since):
                    self.handle_event(event)
                ok = self.clock() - opened >= 5
                # Proxies close idle streams (HAProxy after 10 minutes): resume where it stopped.
                log.debug("Docker events stream closed")
            except Exception as exc:  # noqa: BLE001 - the watcher must survive Docker restarts
                ok = False
                log.warning("Docker events unavailable (%s), retrying in %d s", type(exc).__name__, delay)
            # Ask again from one second before the cut: what happened meanwhile is not lost.
            since = int(self.clock()) - 1
            if ok:
                delay = 5
                continue
            self.sleep(delay)
            delay = min(delay * 2, 300)
