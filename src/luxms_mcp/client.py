"""LuxmsClient: the only place that speaks HTTP. Never logs secrets."""
import json
import logging
from typing import Any

import httpx

from .config import Config

log = logging.getLogger("luxms_mcp")


class LuxmsError(Exception):
    """Error with a message that is safe and useful to show to Claude."""


_STATUS_TEXT = {
    401: "401 — токен не подошёл (или сессия истекла)",
    403: "403 — нет прав на этот метод",
    404: "404 — нет такого атласа/куба/объекта",
}


_HINTS = [
    ("INVALID_SESSION", " Сессия не принята: кука или токен истекли — обнови LUXMS_COOKIE/LUXMS_TOKEN в .env."),
    ("вложенные вызовы агрегатных", " Мера уже агрегирована в модели куба: пиши \"profit\", а не \"sum(profit)\"."),
    ("не существует", " Такого столбца нет в кубе — проверь имена через describe_cube."),
    ("does not exist", " Такого столбца нет в кубе — проверь имена через describe_cube."),
]


class LuxmsClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        headers = {"Accept": "application/json"}
        if cfg.token:
            headers["Authorization"] = f"Bearer {cfg.token}"
        elif cfg.cookie:
            headers["Cookie"] = cfg.cookie
        self.http = httpx.Client(base_url=cfg.url, headers=headers, timeout=cfg.timeout)
        self._logged_in = bool(cfg.token or cfg.cookie)

    @property
    def auth_mode(self) -> str:
        c = self.cfg
        return "token" if c.token else "cookie" if c.cookie else "login" if c.user else "none"

    # --- auth (fallback session mode) -------------------------------------
    def _login(self) -> None:
        if not (self.cfg.user and self.cfg.password):
            raise LuxmsError("401 — не задан ни LUXMS_TOKEN, ни LUXMS_COOKIE, ни LUXMS_USER/LUXMS_PASSWORD")
        r = self.http.post(
            self.cfg.login_path,
            json={"username": self.cfg.user, "password": self.cfg.password},
        )
        if r.status_code >= 400:
            raise LuxmsError(f"Вход по логину не удался: HTTP {r.status_code}")
        self._logged_in = True

    # --- core request -----------------------------------------------------
    def request(self, method: str, path: str, *, json_body: Any = None,
                params: dict | None = None, write: bool = False) -> Any:
        if not self._logged_in:
            self._login()
        retries = 0 if write else 1
        relogin = self.auth_mode == "login"
        attempt = 0
        while True:
            try:
                r = self.http.request(method, path, json=json_body, params=params)
            except httpx.TransportError as e:
                if attempt < retries:
                    attempt += 1
                    continue
                raise LuxmsError(f"Сеть: {type(e).__name__} при {method} {path}") from None
            if r.status_code == 401 and relogin:
                relogin = False
                self._logged_in = False
                self._login()
                continue
            if r.status_code >= 500 and attempt < retries:
                attempt += 1
                continue
            break
        if write:
            log.info("WRITE %s %s -> %s", method, path, r.status_code)
        if r.status_code >= 400:
            head = _STATUS_TEXT.get(r.status_code, f"HTTP {r.status_code}")
            hint = next((h for k, h in _HINTS if k in r.text), "")
            raise LuxmsError(f"{head}. {method} {path}.{hint} Ответ: {r.text[:500]}")
        return self._parse(r)

    @staticmethod
    def _parse(r: httpx.Response) -> Any:
        ctype = r.headers.get("content-type", "")
        text = r.text
        if not text.strip():
            return None
        if "stream+json" in ctype:
            return [json.loads(line) for line in text.splitlines() if line.strip()]
        try:
            return r.json()
        except ValueError:
            # some endpoints answer stream+json without the header
            try:
                return [json.loads(line) for line in text.splitlines() if line.strip()]
            except ValueError:
                return text

    def get(self, path: str, **kw): return self.request("GET", path, **kw)
    def post(self, path: str, body: Any, **kw): return self.request("POST", path, json_body=body, **kw)
