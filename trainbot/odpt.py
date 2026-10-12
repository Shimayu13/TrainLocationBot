"""Bounded synchronous HTTP transport, called in worker threads by Discord."""
import copy
import threading
import time
from urllib.parse import parse_qsl, urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import Settings


class ODPTError(RuntimeError):
    """Safe to display: never contains an authenticated URL."""


class ODPTClient:
    def __init__(self, settings=None):
        self.settings = settings or Settings.load()
        self._local = threading.local()
        self._cache = {}
        self._lock = threading.RLock()

    def _session(self):
        if not hasattr(self._local, "session"):
            session = requests.Session()
            retry = Retry(total=2, backoff_factor=0.5, status_forcelist=(429, 502, 503, 504),
                          allowed_methods=("GET",), respect_retry_after_header=False)
            session.mount("https://", HTTPAdapter(max_retries=retry))
            self._local.session = session
        return self._local.session

    def fetch(self, kind, operator, **filters):
        operator = operator.removeprefix("odpt.Operator:")
        endpoint = "public" if operator == "Toei" else "standard" if operator in {
            "YokohamaMunicipal", "TokyoMetro", "TamaMonorail", "TWR", "MIR"
        } else "challenge"
        base = getattr(self.settings, endpoint + "_url")
        key = "" if endpoint == "public" else getattr(self.settings, endpoint + "_key")
        if endpoint != "public" and not key:
            variable = "ODPT_STANDARD_CONSUMER_KEY" if endpoint == "standard" else "ODPT_CHALLENGE_CONSUMER_KEY"
            raise ODPTError(f"{variable} が未設定です。")
        params = {"odpt:operator": "odpt.Operator:" + operator, **filters}
        cache_key = (base, kind, tuple(sorted(params.items())))
        ttl = 86400 if kind in {"Station", "Railway"} else 15
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached and time.monotonic() - cached[0] < ttl:
                return copy.deepcopy(cached[1])
        if key:
            params["acl:consumerKey"] = key
        try:
            response = self._session().get(f"{base}/odpt:{kind}", params=params, timeout=(5, 15))
            response.raise_for_status()
            data = response.json()
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "unknown"
            raise ODPTError(f"{operator}: API HTTP {status}。認証・提供状況を確認してください。") from None
        except (requests.RequestException, ValueError):
            raise ODPTError(f"{operator}: API通信またはJSON解析に失敗しました。") from None
        if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
            raise ODPTError(f"{operator}: API応答の形式が不正です。")
        with self._lock:
            if len(self._cache) >= 256:
                self._cache.pop(next(iter(self._cache)))
            self._cache[cache_key] = (time.monotonic(), data)
        return copy.deepcopy(data)


_client = None
_client_lock = threading.Lock()


def default_client():
    global _client
    with _client_lock:
        if _client is None:
            _client = ODPTClient()
    return _client


class LegacyResponse:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data

    def raise_for_status(self):
        pass


def odpt_get(url, **kwargs):
    """Re-route old URLs without retaining their embedded credentials or host."""
    parsed = urlsplit(url)
    params = dict(parse_qsl(parsed.query))
    operator = params.pop("odpt:operator")
    params.pop("acl:consumerKey", None)
    kind = parsed.path.rsplit("odpt:", 1)[-1]
    return LegacyResponse(default_client().fetch(kind, operator, **params))
