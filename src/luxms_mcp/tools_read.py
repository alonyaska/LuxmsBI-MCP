"""Read tools. All paths verified on hackathon20 (ds_demo171)."""
from typing import Any

from .client import LuxmsClient, LuxmsError


def _rows(data: Any) -> list[dict]:
    if isinstance(data, dict):
        for k in ("rows", "data", "items"):
            if isinstance(data.get(k), list):
                data = data[k]
                break
    return [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []


def _pick(item: dict, keys: tuple[str, ...]) -> dict:
    return {k: item[k] for k in keys if k in item}


def _match(item: dict, search: str | None) -> bool:
    return not search or search.lower() in str(item).lower()


def _q(value: str) -> str:
    """Quote a value for a .filter(...) suffix; reject anything that could break out."""
    if "'" in value or ")" in value or "&" in value or "|" in value:
        raise LuxmsError(f"Недопустимые символы в имени: {value!r}")
    return f"'{value}'"


def split_cube(cube: str) -> tuple[str, str]:
    """'luxmsbi._orders_full_2' -> ('luxmsbi', '_orders_full_2')."""
    if "." not in cube:
        raise LuxmsError("cube задаётся как 'источник.имя', например 'luxmsbi._orders_full_2'")
    src, name = cube.split(".", 1)
    return src, name


def dashlet_summary(d: dict) -> dict:
    cfg = d.get("config") or {}
    ds = cfg.get("dataSource") or {}
    filters = ds.get("filters") or {}
    return {
        "id": d.get("id"), "title": d.get("title"), "view_class": d.get("view_class"),
        "cube": ds.get("koob"), "dimensions": ds.get("dimensions"), "measures": ds.get("measures"),
        # filters == true means "available but not set": drop them
        "filters": {k: v for k, v in filters.items() if v is not True},
        "frame": cfg.get("frame"), "parent_id": d.get("parent_id"),
    }


class ReadTools:
    def __init__(self, client: LuxmsClient):
        self.c = client

    def ping(self) -> dict:
        cfg = self.c.cfg
        out = {"url": cfg.url, "readonly": cfg.readonly, "auth": self.c.auth_mode}
        try:
            out["atlases_visible"] = len(self.list_atlases())
        except LuxmsError as e:
            out["error"] = str(e)
        return out

    def list_atlases(self, search: str | None = None) -> list[dict]:
        data = _rows(self.c.get("/api/db/adm.datasets/"))
        out = [_pick(a, ("id", "schema_name", "title", "description")) for a in data]
        return [a for a in out if _match(a, search)]

    def resolve_atlas(self, atlas: str) -> str:
        """Accept schema ('ds_demo171') or title ('Fashion Evolution')."""
        if atlas.startswith("ds_"):
            return atlas
        all_ = self.list_atlases()
        exact = [a for a in all_ if str(a.get("title", "")).lower() == atlas.lower()]
        hits = exact or [a for a in all_ if atlas.lower() in str(a.get("title", "")).lower()]
        if len(hits) == 1 and hits[0].get("schema_name"):
            return hits[0]["schema_name"]
        names = [f"{a.get('title')} ({a.get('schema_name')})" for a in (hits or all_)]
        raise LuxmsError(f"404 — атлас «{atlas}» не найден однозначно. Есть: {names}")

    def list_cubes(self, atlas: str, search: str | None = None,
                   include_global: bool = False) -> list[dict]:
        s = self.resolve_atlas(atlas)
        suffix = "" if include_global else ".filter(is_global=0)"
        out = []
        for x in _rows(self.c.get(f"/api/db/{s}.cubes/{suffix}")):
            item = _pick(x, ("id", "title", "is_global"))
            item["cube"] = f"{x.get('source_ident')}.{x.get('name')}"
            out.append(item)
        return [x for x in out if _match(x, search)]

    def describe_cube(self, atlas: str, cube: str, search: str | None = None) -> list[dict]:
        s = self.resolve_atlas(atlas)
        src, name = split_cube(cube)
        flt = f".filter(source_ident={_q(src)}&&cube_name={_q(name)})"
        data = _rows(self.c.get(f"/api/db/{s}.dimensions/{flt}"))
        if not data:
            raise LuxmsError(f"404 — у куба «{cube}» в атласе {s} нет измерений. Проверь list_cubes.")
        drop = ("source_ident", "cube_name", "created", "updated", "guid")
        out = [{k: v for k, v in d.items() if k not in drop and v not in (None, "", {}, [])}
               for d in data]
        return [d for d in out if _match(d, search)]

    def _body(self, cube: str, columns: list[str], filters: dict | None,
              sort: list[str] | None) -> dict:
        split_cube(cube)
        body: dict[str, Any] = {"with": cube, "columns": columns}
        if filters:
            body["filters"] = filters
        if sort:
            body["sort"] = sort
        return body

    def query_cube(self, atlas: str, cube: str, columns: list[str], filters: dict | None = None,
                   sort: list[str] | None = None, limit: int | None = None) -> dict:
        s = self.resolve_atlas(atlas)
        n = min(limit or self.c.cfg.max_rows, self.c.cfg.max_rows)
        body = self._body(cube, columns, filters, sort) | {"limit": n + 1}
        rows = self.c.post(f"/api/v3/{s}/data", body)
        rows = rows if isinstance(rows, list) else _rows(rows)
        return {"rows": rows[:n], "truncated": len(rows) > n}

    def explain_query(self, atlas: str, cube: str, columns: list[str], filters: dict | None = None,
                      sort: list[str] | None = None) -> Any:
        s = self.resolve_atlas(atlas)
        return self.c.post(f"/api/v3/{s}/data?meta", self._body(cube, columns, filters, sort))

    def count_rows(self, atlas: str, cube: str, columns: list[str],
                   filters: dict | None = None) -> Any:
        s = self.resolve_atlas(atlas)
        return self.c.post(f"/api/v3/{s}/count", self._body(cube, columns, filters, None))

    def list_dashboards(self, atlas: str) -> list[dict]:
        s = self.resolve_atlas(atlas)
        boards = _rows(self.c.get(f"/api/db/{s}.dashboards/.order_by(srt,id)"))
        counts: dict[Any, int] = {}
        for d in _rows(self.c.get(f"/api/db/{s}.dashlets/")):
            counts[d.get("dashboard_id")] = counts.get(d.get("dashboard_id"), 0) + 1
        return [_pick(b, ("id", "title", "is_hidden")) | {"dashlets": counts.get(b.get("id"), 0),
                                                           "url": self.dashboard_url(s, b.get("id"))}
                for b in boards]

    def raw_dashboard(self, schema: str, dashboard_id: int) -> dict:
        hits = [b for b in _rows(self.c.get(f"/api/db/{schema}.dashboards/"))
                if b.get("id") == dashboard_id]
        if not hits:
            raise LuxmsError(f"404 — в атласе {schema} нет дашборда {dashboard_id}")
        return hits[0]

    def raw_dashlets(self, schema: str, dashboard_id: int | None = None) -> list[dict]:
        data = _rows(self.c.get(f"/api/db/{schema}.dashlets/"))
        return [d for d in data if dashboard_id is None or d.get("dashboard_id") == dashboard_id]

    def get_dashboard(self, atlas: str, dashboard_id: int, raw_config: bool = False) -> dict:
        s = self.resolve_atlas(atlas)
        board = self.raw_dashboard(s, dashboard_id)
        dashlets = self.raw_dashlets(s, dashboard_id)
        return {"id": board.get("id"), "title": board.get("title"),
                "url": self.dashboard_url(s, dashboard_id),
                "dashlets": dashlets if raw_config else [dashlet_summary(d) for d in dashlets]}

    def dashboard_url(self, schema: str, dashboard_id: Any) -> str:
        return f"{self.c.cfg.url}/#/ds/{schema}/dashboards?dboard={dashboard_id}"
