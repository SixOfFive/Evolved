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
        """Return a parsed JSON dict from the model, or None on any failure."""
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
            if r.status_code != 200:
                return None
            data = r.json()
            content = data.get("message", {}).get("content", "")
            return _extract_json(content)
        except Exception:
            return None


class LLMManager:
    """Threaded, coalescing request pump around an OllamaClient."""

    def __init__(self, client, enabled=True):
        self.client = client
        self.enabled = enabled
        self._jobs = queue.Queue()
        self._results = queue.Queue()
        self._inflight = set()
        self._inflight_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self.stats = {"requests": 0, "ok": 0, "fail": 0}

    def start(self):
        if not self.enabled:
            return
        self._thread = threading.Thread(target=self._worker, daemon=True,
                                        name="llm-worker")
        self._thread.start()

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

    def _worker(self):
        while not self._stop.is_set():
            try:
                cell_id, system, user = self._jobs.get(timeout=0.5)
            except queue.Empty:
                continue
            result = None
            try:
                result = self.client.chat_json(system, user)
            except Exception:
                result = None
            if result is not None:
                self.stats["ok"] += 1
            else:
                self.stats["fail"] += 1
            self._results.put((cell_id, result))
            with self._inflight_lock:
                self._inflight.discard(cell_id)

    def stop(self):
        self._stop.set()
