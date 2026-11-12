import os
import re
from dataclasses import dataclass
from pathlib import Path

PROJECT_ENV = Path(__file__).resolve().parents[2] / ".env"


def load_dotenv() -> None:
    """Fill os.environ from .env (cwd first, then project root). Real env vars win.
    Values may be quoted; an unquoted ' # ...' or '#...' after the value is a comment."""
    for f in (Path.cwd() / ".env", PROJECT_ENV):
        if not f.is_file():
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
            if not m or line.lstrip().startswith("#"):
                continue
            key, val = m.group(1), m.group(2).strip()
            if val[:1] in "\"'" and len(val) > 1 and val[-1] == val[0]:
                val = val[1:-1]
            else:
                val = val.split("#", 1)[0].strip()
            os.environ.setdefault(key, val)


def _bool(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() not in ("0", "false", "no", "")


@dataclass(frozen=True)
class Config:
    url: str
    token: str
    user: str
    password: str
    readonly: bool
    timeout: float
    max_rows: int
    cookie: str
    login_path: str

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()
        url = os.getenv("LUXMS_URL", "").rstrip("/")
        if not url:
            raise RuntimeError("LUXMS_URL is required")
        return cls(
            url=url,
            token=os.getenv("LUXMS_TOKEN", ""),
            user=os.getenv("LUXMS_USER", ""),
            password=os.getenv("LUXMS_PASSWORD", ""),
            readonly=_bool("LUXMS_READONLY", "1"),
            timeout=float(os.getenv("LUXMS_TIMEOUT", "30")),
            max_rows=int(os.getenv("LUXMS_MAX_ROWS", "200")),
            cookie=os.getenv("LUXMS_COOKIE", ""),
            login_path=os.getenv("LUXMS_LOGIN_PATH", "/api/auth/login"),
        )
