"""Ollama integration for rival cell strategy.

The LLM is used as a *policy oracle*, not a per-frame controller: a background
worker thread asks the model (qwen3:4b on Ollama) for a high-level plan every
few seconds, and the AI's heuristics execute that plan smoothly every frame.
This keeps the game at 60 FPS even when the model answers slowly.

Nothing in here touches game objects directly. Requests go in via `request()`,
answers come out through `drain_results()` which the main thread polls.
"""

import json
import queue
import re
import threading
import time

import requests

from . import config as C

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def _extract_json(text):
    if not text:
        return None
    text = _THINK_RE.sub("", text).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    # fall back to the first {...} block
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except Exception:
            return None
    return None


class OllamaClient:
    def __init__(self, host, port, model, timeout=C.LLM_TIMEOUT):
        self.base = f"http://{host}:{port}"
        self.model = model
        self.timeout = timeout

    def available(self):
        try:
            r = requests.get(self.base + "/api/tags", timeout=4)
            return r.status_code == 200
        except Exception:
            return False

    def chat_json(self, system, user):
        """Return (parsed dict | None, throttled: bool).

        `throttled` is True when the server answered 429 - the caller should
        back off instead of retrying immediately.
        """
        payload = {
            "model": self.model,
            "stream": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": system + "\n/no_think"},
                {"role": "user", "content": user},
            ],
            "options": {
                "temperature": 0.75,
                "top_p": 0.9,
                "num_predict": 220,
            },
        }
        try:
            r = requests.post(self.base + "/api/chat", json=payload,
                              timeout=self.timeout)
            if r.status_code == 429:
                return None, True
            if r.status_code != 200:
                return None, False
            data = r.json()
            content = data.get("message", {}).get("content", "")
            return _extract_json(content), False
        except Exception:
            return None, False


class LLMManager:
    """Threaded, coalescing request pump around an OllamaClient.

    Several worker threads run in parallel so a burst of requests (e.g. ten
    rivals all making their spawn-time strategy call) doesn't serialize behind
    one slow HTTP round-trip.
    """

    WORKERS = 4

    def __init__(self, client, enabled=True):
        self.client = client
        self.enabled = enabled
        self._jobs = queue.Queue()
        self._results = queue.Queue()
        self._inflight = set()
        self._inflight_lock = threading.Lock()
        self._stop = threading.Event()
        self._threads = []
        self.stats = {"requests": 0, "ok": 0, "fail": 0, "throttled": 0}
        # global politeness gate: a 429 from the server pauses ALL workers,
        # with the pause growing on repeat offenses and resetting on success
        self._gate = 0.0
        self._backoff = 6.0

    def start(self):
        if not self.enabled:
            return
        for i in range(self.WORKERS):
            t = threading.Thread(target=self._worker, daemon=True,
                                 name=f"llm-worker-{i}")
            t.start()
            self._threads.append(t)

    def clear_pending(self):
        """Drop queued (not yet started) jobs and stale results.

        Called on world reset so requests from dead cells don't occupy the
        workers ahead of the new population's spawn decisions.
        """
        for q in (self._jobs, self._results):
            while True:
                try:
                    job = q.get_nowait()
                except queue.Empty:
                    break
                if q is self._jobs:
                    with self._inflight_lock:
                        self._inflight.discard(job[0])

    def busy(self, cell_id):
        with self._inflight_lock:
            return cell_id in self._inflight

    def request(self, cell_id, system, user):
        """Queue a strategy request for a cell (ignored if one is in flight)."""
        if not self.enabled:
            return False
        with self._inflight_lock:
            if cell_id in self._inflight:
                return False
            self._inflight.add(cell_id)
        self.stats["requests"] += 1
        self._jobs.put((cell_id, system, user))
        return True

    def drain_results(self):
        """Return a list of (cell_id, policy_dict) ready to apply. Non-blocking."""
        out = []
        while True:
            try:
                out.append(self._results.get_nowait())
            except queue.Empty:
                break
        return out

    def throttled(self):
        return time.time() < self._gate

    def _worker(self):
        while not self._stop.is_set():
            try:
                cell_id, system, user = self._jobs.get(timeout=0.5)
            except queue.Empty:
                continue
            # honor the 429 backoff gate before touching the server
            while not self._stop.is_set() and time.time() < self._gate:
                time.sleep(0.25)
            result, throttled = None, False
            try:
                result, throttled = self.client.chat_json(system, user)
            except Exception:
                result = None
            if throttled:
                self.stats["throttled"] += 1
                self._gate = time.time() + self._backoff
                self._backoff = min(60.0, self._backoff * 1.7)
            elif result is not None:
                self.stats["ok"] += 1
                self._backoff = 6.0
            else:
                self.stats["fail"] += 1
            self._results.put((cell_id, result))
            with self._inflight_lock:
                self._inflight.discard(cell_id)

    def stop(self):
        self._stop.set()
