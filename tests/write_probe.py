"""Probe write API on the TRAINING stand (ds_demo171). Standalone, not via MCP tools.

  uv run python tests/write_probe.py            # dry-run: only prints the plan
  uv run python tests/write_probe.py --send     # steps 0-3: create dashboard + dashlet, print link
  uv run python tests/write_probe.py --finish   # steps 5-6: update title, delete both, verify

Rules: creates only "[mcp] test ..." objects; PUT/PATCH/DELETE only on ids returned by our own
POST (kept in tests/.write_probe_state.json) whose title starts with "[mcp] test"; stops on the
first error; never prints credentials. Foreign objects are snapshotted before every mutation and
compared after it: if anything foreign changed, the script stops immediately.
"""
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
STATE = Path(__file__).with_name(".write_probe_state.json")
ALLOWED_HOST = "hackathon20.a2c.luxmsbi.ru"   # training stand only
S = "ds_demo171"
PREFIX = "[mcp] test"
BOARDS, LETS = f"/api/db/{S}.dashboards/", f"/api/db/{S}.dashlets/"

DASHBOARD = {"title": "[mcp] test dashboard", "topic_id": 0,
             "config": {"icon": "dashboard", "viewport": {"h": 8, "w": 12}, "_mcp": True}}
DASHLET = {"dashboard_id": None, "view_class": "1II.bar", "title": "[mcp] test chart",
           "config": {"frame": {"x": 0, "y": 0, "w": 6, "h": 7},
                      "dataSource": {"koob": "luxmsbi._orders_full_2",
                                     "xAxis": "cust_companyname", "yAxis": "measures",
                                     "dimensions": ["cust_companyname"], "measures": ["profit"],
                                     "filters": {"categoryname": ["=", "Женская обувь"]}},
                      "display": {"sort": "DESC", "sortBy": "profit"},
                      "_mcp": True}}
NEW_TITLE = "[mcp] test chart (updated)"


def load_env() -> dict:
    env = {}
    f = ROOT / ".env"
    if not f.exists():
        sys.exit("STOP: нет файла .env (нужны LUXMS_URL и LUXMS_TOKEN или LUXMS_COOKIE)")
    for line in f.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


ENV = load_env()
URL = ENV.get("LUXMS_URL", "").rstrip("/")
SECRETS = [v for k in ("LUXMS_TOKEN", "LUXMS_COOKIE", "LUXMS_PASSWORD") if (v := ENV.get(k))]
if httpx.URL(URL).host != ALLOWED_HOST:
    sys.exit(f"STOP: LUXMS_URL должен указывать на тренировочный стенд {ALLOWED_HOST}")
headers = {"Accept": "application/json"}
if ENV.get("LUXMS_TOKEN"):
    headers["Authorization"] = f"Bearer {ENV['LUXMS_TOKEN']}"
elif ENV.get("LUXMS_COOKIE"):
    headers["Cookie"] = ENV["LUXMS_COOKIE"]
else:
    sys.exit("STOP: в .env нет ни LUXMS_TOKEN, ни LUXMS_COOKIE")
http = httpx.Client(base_url=URL, headers=headers, timeout=30)
state: dict = json.loads(STATE.read_text()) if STATE.exists() else {}
table: list[tuple] = []


def save() -> None:
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def clean(text: str) -> str:
    for s in SECRETS:
        text = text.replace(s, "***")
    return text


def stop(msg: str) -> None:
    print(f"\nSTOP: {msg}")
    left = {k: state[k] for k in ("dashboard_id", "dashlet_id") if state.get(k) is not None}
    if left:
        print(f"На стенде остались наши тестовые объекты: {left} (удалит --finish)")
    summary()
    sys.exit(1)


def call(step: str, method: str, path: str, body=None, *, quiet=False, expect_fail=False):
    """Send, print method/path/status/body. Stops on non-2xx unless expect_fail."""
    r = http.request(method, path, json=body)
    text = clean(r.text)
    print(f"\n[{step}] {method} {path} -> {r.status_code} ({r.headers.get('content-type', '')})")
    if body is not None:
        print("  отправлено:", json.dumps(body, ensure_ascii=False))
    if quiet and r.is_success:
        print(f"  ответ: {len(text)} байт (список, целиком не печатаю)")
    else:
        print("  ответ:", text if text else "<пусто>")
    try:
        data = r.json() if r.text.strip() else None
    except ValueError:
        data = r.text
    table.append((step, f"{method} {path}", r.status_code, shape(data), r.is_success))
    if not r.is_success and not expect_fail:
        stop(f"шаг {step}: HTTP {r.status_code}")
    return r, data


def shape(data) -> str:
    if data is None:
        return "пусто"
    if isinstance(data, list):
        return f"массив[{len(data)}]"
    if isinstance(data, dict):
        return "объект{" + ",".join(list(data)[:6]) + "}"
    return "текст"


def rows(step: str, path: str) -> list[dict]:
    _, data = call(step, "GET", path, quiet=True)
    if not isinstance(data, list):
        stop(f"шаг {step}: ожидался массив")
    return data


def find(items: list[dict], id_) -> dict | None:
    return next((x for x in items if x.get("id") == id_), None)


def new_id(data, step: str, list_path: str, title: str, before_ids: set):
    """id from POST response; fallback: the single new row with our title."""
    obj = data[0] if isinstance(data, list) and data else data
    if isinstance(obj, dict) and obj.get("id") is not None:
        return obj["id"], "из ответа POST"
    mine = [x for x in rows(step + " (поиск id)", list_path)
            if x.get("title") == title and x.get("id") not in before_ids]
    if len(mine) != 1:
        stop(f"шаг {step}: id не пришёл и по title найдено {len(mine)} новых объектов")
    return mine[0]["id"], "повторным GET по title"


def guard(kind: str, id_, obj: dict | None) -> None:
    """Mutations only on our own test objects."""
    if id_ is None or id_ != state.get(f"{kind}_id"):
        stop(f"guard: {kind} id={id_} не из нашего POST")
    if not obj or not str(obj.get("title", "")).startswith(PREFIX):
        stop(f"guard: {kind} id={id_} не найден или title не начинается с «{PREFIX}»")


def foreign_snapshot() -> dict:
    snap = {}
    for name, path, own in (("dashboards", BOARDS, state.get("dashboard_id")),
                            ("dashlets", LETS, state.get("dashlet_id"))):
        r = http.get(path)
        if not r.is_success:
            stop(f"snapshot {path}: HTTP {r.status_code}")
        snap[name] = {str(x["id"]): x for x in r.json() if x.get("id") != own}
    return snap


def assert_foreign_untouched(before: dict, what: str) -> None:
    after = foreign_snapshot()
    for name in before:
        strip = lambda d: {i: {k: v for k, v in x.items() if k != "updated"} for i, x in d.items()}
        if strip(before[name]) != strip(after[name]):
            bak = Path(__file__).with_name(".write_probe_foreign_backup.json")
            bak.write_text(json.dumps(before, ensure_ascii=False, indent=2))
            changed = [i for i in before[name] if strip(before[name]).get(i) != strip(after[name]).get(i)]
            stop(f"ЧУЖИЕ {name} изменились после «{what}»: id {changed}. Снимок «до»: {bak}")


def summary() -> None:
    if table:
        print("\n| шаг | запрос | статус | ответ | ок |\n|---|---|---|---|---|")
        for step, req, status, shp, ok in table:
            print(f"| {step} | {req} | {status} | {shp} | {'да' if ok else 'нет'} |")
    facts = {k: v for k, v in state.items() if k not in ("dashboard_id", "dashlet_id")}
    if facts:
        print("\nВыводы:", json.dumps(facts, ensure_ascii=False, indent=2))


def dry_run() -> None:
    print(f"DRY-RUN. Стенд {URL}, атлас {S}. Ничего не отправлено.\n")
    plan = [
        ("0", "GET", f"/api/can/edit_dashboards/{S}/", None),
        ("1", "POST", BOARDS, DASHBOARD),
        ("2", "GET", BOARDS, None),
        ("3", "POST", LETS, DASHLET | {"dashboard_id": "<id из шага 1>"}),
        ("4", "—", f"{URL}/#/ds/{S}/dashboards?dboard=<id> (проверка глазами)", None),
        ("5a", "PUT", LETS + ".filter(id=<id>)", {"title": NEW_TITLE}),
        ("5a'", "PUT", LETS + ".filter(id=<id>)", "<полный объект из GET с новым title>"),
        ("5b", "PATCH", LETS + ".filter(id=<id>)", {"title": NEW_TITLE}),
        ("5c", "PUT", LETS + "<id>", {"title": NEW_TITLE}),
        ("6", "DELETE", LETS + ".filter(id=<id>)  (или формат, сработавший в шаге 5)", None),
        ("6", "DELETE", BOARDS + ".filter(id=<id>)", None),
        ("6", "GET", "оба списка: проверить, что наших объектов нет", None),
    ]
    for step, method, path, body in plan:
        print(f"[{step}] {method} {path}")
        if body is not None:
            print("     ", json.dumps(body, ensure_ascii=False))
    print("\nОтправка: --send (шаги 0-3), затем --finish (шаги 5-6).")


def send() -> None:
    if state.get("dashboard_id") is not None:
        stop("в state уже есть тестовые объекты от прошлого запуска — сначала --finish")
    call("0", "GET", f"/api/can/edit_dashboards/{S}/")

    before = {x.get("id") for x in rows("1 (до)", BOARDS)}
    _, data = call("1", "POST", BOARDS, DASHBOARD)
    state["post_dashboard_returns"] = shape(data)
    state["dashboard_id"], state["dashboard_id_source"] = new_id(data, "1", BOARDS, DASHBOARD["title"], before)
    save()

    mine = find(rows("2", BOARDS), state["dashboard_id"])
    if not mine:
        stop("шаг 2: свой дашборд не найден в списке")
    print("  наш дашборд:", json.dumps(mine, ensure_ascii=False))
    state["mcp_flag_kept_dashboard"] = (mine.get("config") or {}).get("_mcp") is True
    save()

    before = {x.get("id") for x in rows("3 (до)", LETS)}
    body = DASHLET | {"dashboard_id": state["dashboard_id"]}
    _, data = call("3", "POST", LETS, body)
    state["post_dashlet_returns"] = shape(data)
    state["dashlet_id"], state["dashlet_id_source"] = new_id(data, "3", LETS, DASHLET["title"], before)
    save()
    mine = find(rows("3 (чтение)", LETS), state["dashlet_id"])
    print("  наш дашлет:", json.dumps(mine, ensure_ascii=False))
    state["mcp_flag_kept_dashlet"] = bool(mine) and (mine.get("config") or {}).get("_mcp") is True
    save()

    print(f"\n[4] Открой и проверь, что график нарисовался:\n    "
          f"{URL}/#/ds/{S}/dashboards?dboard={state['dashboard_id']}\n    Потом: --finish")


def title_now(step: str) -> str | None:
    obj = find(rows(step, LETS), state["dashlet_id"])
    return obj.get("title") if obj else None


def finish() -> None:
    did, lid = state.get("dashboard_id"), state.get("dashlet_id")
    if did is None:
        stop("в state нет созданных объектов — нечего завершать")
    by_filter = lambda base, i: f"{base}.filter(id={i})"
    by_id = lambda base, i: f"{base}{i}"
    fmt = state.get("update_format")

    if lid is not None and not fmt:
        cur = find(rows("5 (до)", LETS), lid)
        guard("dashlet", lid, cur)
        full = {k: v for k, v in cur.items() if k not in ("created", "updated")} | {"title": NEW_TITLE}
        attempts = [("5a", "PUT", by_filter(LETS, lid), {"title": NEW_TITLE}, "filter", "частичное"),
                    ("5a'", "PUT", by_filter(LETS, lid), full, "filter", "полное"),
                    ("5b", "PATCH", by_filter(LETS, lid), {"title": NEW_TITLE}, "filter", "частичное"),
                    ("5c", "PUT", by_id(LETS, lid), {"title": NEW_TITLE}, "id", "частичное")]
        for step, method, path, body, addr, kind in attempts:
            snap = foreign_snapshot()
            r, _ = call(step, method, path, body, expect_fail=True)
            assert_foreign_untouched(snap, f"{method} {path}")
            after = find(rows(step + " (проверка)", LETS), lid)
            if not after:
                state["dashlet_id"] = None
                save()
                stop(f"шаг {step}: наш дашлет исчез после {method}")
            if r.is_success and after.get("title") == NEW_TITLE:
                state["update_format"] = {"method": method, "address": addr, "body": kind}
                state["update_kept_config"] = after.get("config") == cur.get("config")
                save()
                break
        else:
            stop("шаг 5: ни один формат изменения не сработал")
        fmt = state["update_format"]

    addr = by_id if (fmt or {}).get("address") == "id" else by_filter
    for kind, base, id_ in (("dashlet", LETS, lid), ("dashboard", BOARDS, did)):
        if id_ is None:
            continue
        guard(kind, id_, find(rows(f"6 ({kind}, до)", base), id_))
        snap = foreign_snapshot()
        _, data = call("6", "DELETE", addr(base, id_))
        state[f"{kind}_id"] = None
        state["delete_format"] = {"address": (fmt or {}).get("address", "filter"), "returns": shape(data)}
        save()
        assert_foreign_untouched(snap, f"DELETE {kind}")

    left = [x for path in (BOARDS, LETS) for x in rows("6 (проверка)", path)
            if x.get("id") in (did, lid) and str(x.get("title", "")).startswith(PREFIX)]
    if left:
        stop("после удаления остались: " + json.dumps([(x['id'], x['title']) for x in left], ensure_ascii=False))
    print("\nЧисто: тестовых объектов на стенде нет.")


if "--send" in sys.argv:
    send()
elif "--finish" in sys.argv:
    finish()
else:
    dry_run()
summary()
