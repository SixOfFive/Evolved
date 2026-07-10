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
        """Return (parsed dict | None, diag dict).

        diag["kind"] is one of: ok, 429, http, timeout, conn, badjson, error.
        diag["detail"] is a short human explanation; diag["latency"] is the
        round-trip in seconds when a response actually came back.
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
        t0 = time.time()
        try:
            r = requests.post(self.base + "/api/chat", json=payload,
                              timeout=self.timeout)
            latency = time.time() - t0
            if r.status_code == 429:
                return None, {"kind": "429", "latency": latency,
                              "detail": "rate-limited (HTTP 429)"}
            if r.status_code != 200:
                return None, {"kind": "http", "latency": latency,
                              "detail": f"HTTP {r.status_code} from server"}
            data = r.json()
            content = data.get("message", {}).get("content", "")
            parsed = _extract_json(content)
            if parsed is None:
                return None, {"kind": "badjson", "latency": latency,
                              "detail": "unparseable reply from model"}
            return parsed, {"kind": "ok", "latency": latency, "detail": "ok"}
        except requests.exceptions.Timeout:
            return None, {"kind": "timeout", "latency": None,
                          "detail": f"timeout after {self.timeout:.0f}s"}
        except requests.exceptions.ConnectionError as e:
            return None, {"kind": "conn", "latency": None,
                          "detail": f"connection error: {str(e)[:60]}"}
        except Exception as e:
            return None, {"kind": "error", "latency": None,
                          "detail": f"{type(e).__name__}: {str(e)[:60]}"}


class LLMManager:
    """Threaded, coalescing request pump around an OllamaClient.

    Several worker threads run in parallel so a burst of requests (e.g. ten
    rivals all making their spawn-time strategy call) doesn't serialize behind
    one slow HTTP round-trip.
    """

    WORKERS = 4

    def __init__(self, client, enabled=True, disabled_reason=""):
        self.client = client
        self.enabled = enabled
        self.disabled_reason = disabled_reason or "LLM disabled"
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
        # health diagnostics for the in-game feed
        self._last_ok = None          # time.time() of the last good reply
        self._last_error = ""         # human text of the last failure
        self._consec_fail = 0
        self._first_request = None
        self._latencies = []          # last few successful round-trips

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
        if self._first_request is None:
            self._first_request = time.time()
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

    def avg_latency(self):
        if not self._latencies:
            return None
        return sum(self._latencies) / len(self._latencies)

    def heuristics_reason(self):
        """Why heuristics are driving right now - or None if the LLM is fine.

        The verdict: healthy means a good reply landed within the last 20s.
        Everything else produces a human-readable explanation for the feed.
        """
        if not self.enabled:
            return self.disabled_reason
        now = time.time()
        if now < self._gate:
            msg = f"{self._last_error or 'rate-limited'}; retrying in " \
                  f"{int(self._gate - now) + 1}s"
            return self._with_history(msg, now)
        if self._first_request is None:
            return None  # nothing has been asked of it yet
        if self._last_ok is not None and now - self._last_ok < 20.0:
            return None  # healthy
        if self._last_ok is None and self._consec_fail == 0 \
                and self.stats["throttled"] == 0:
            waited = now - self._first_request
            if waited < 15.0:
                return None  # give the first request a fair chance
            return f"no reply yet, first request sent {int(waited)}s ago " \
                   f"(model may be cold-loading)"
        msg = self._last_error or "no recent replies"
        if self._consec_fail > 1:
            msg += f" ({self._consec_fail} in a row)"
        return self._with_history(msg, now)

    def _with_history(self, msg, now):
        parts = [msg]
        if self._last_ok is not None:
            parts.append(f"last good reply {int(now - self._last_ok)}s ago")
        avg = self.avg_latency()
        if avg is not None:
            parts.append(f"avg round-trip {avg:.1f}s")
        return "; ".join(parts)

    def _worker(self):
        while not self._stop.is_set():
            try:
                cell_id, system, user = self._jobs.get(timeout=0.5)
            except queue.Empty:
                continue
            # honor the 429 backoff gate before touching the server
            while not self._stop.is_set() and time.time() < self._gate:
                time.sleep(0.25)
            result, diag = None, {"kind": "error", "detail": "internal"}
            try:
                result, diag = self.client.chat_json(system, user)
            except Exception as e:
                diag = {"kind": "error", "latency": None,
                        "detail": f"{type(e).__name__}"}
            kind = diag.get("kind")
            if kind == "429":
                self.stats["throttled"] += 1
                self._last_error = diag["detail"]
                self._gate = time.time() + self._backoff
                self._backoff = min(60.0, self._backoff * 1.7)
            elif kind == "ok":
                self.stats["ok"] += 1
                self._backoff = 6.0
                self._last_ok = time.time()
                self._consec_fail = 0
                self._latencies.append(diag["latency"])
                del self._latencies[:-10]
            else:
                self.stats["fail"] += 1
                self._consec_fail += 1
                self._last_error = diag.get("detail", "unknown error")
            self._results.put((cell_id, result))
            with self._inflight_lock:
                self._inflight.discard(cell_id)

    def stop(self):
        self._stop.set()
