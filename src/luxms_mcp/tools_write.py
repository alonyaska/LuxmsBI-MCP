"""Write tools. dry_run=True by default.

Verified on the training stand (tests/write_probe.py, ds_demo171):
  POST   /api/db/{schema}.dashboards/              -> created object with id
  POST   /api/db/{schema}.dashlets/                -> created object with id
  PUT    /api/db/{schema}.dashlets/.filter(id=N)   partial body, config untouched -> [obj]
  DELETE /api/db/{schema}.{table}/.filter(id=N)    -> [deleted obj]
The `_mcp` flag inside config survives the round trip; dashlet frame lives in config.frame.

Dashlet formats copied from existing dashboards on the stand:
  KPI number      view_class "text", measures ["expr:alias"], display.format "# ##0"
  heading/notes   view_class "internal", config.url "res:CustomHTML.js", name + text
  filter panel    view_class "VizelKoobControl", dataSource.dimensions + xAxis "a;b"
  react to panel  chart dataSource.filters {"dim": true}; fixed filter {"dim": ["=", ...]}
"""
import re
from typing import Any

from .client import LuxmsClient, LuxmsError

MARK = "_mcp"  # config flag on objects we created; others need force=True
GRID_W = 12

VIEW_CLASS = {"bar": "1II.bar", "column": "1II.column", "line": "1II.line", "area": "1II.area",
              "spline": "1II.spline", "stacked": "1II.stacked-column", "table": "1II.table",
              "waterfall": "1II.waterfall", "funnel": "1I1.funnel", "treemap": "111.treemap",
              "gauge": "111.gauge", "radar": "111.radar", "heatmap": "heat-map",
              "pie": "1I1.bublik", "donut": "1I1.bublik"}
_FN = re.compile(r"^\s*([a-z_]+)\s*\(\s*(?:distinct\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\)\s*$", re.I)


def _ours(obj: dict) -> bool:
    return bool((obj.get("config") or {}).get(MARK))


def _measure(m: str) -> tuple[str, str]:
    """'sum(sales)' -> ('sum(sales)', 'sum_sales'); 'profit' -> ('profit', 'profit');
    'sum(sales):s' -> ('sum(sales)', 's')."""
    if ":" in m:
        expr, alias = m.rsplit(":", 1)
        return expr.strip(), alias.strip()
    fm = _FN.match(m)
    if fm:
        return m.strip(), f"{fm.group(1).lower()}_{fm.group(2)}"
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", m.strip()):
        return m.strip(), m.strip()
    alias = re.sub(r"\W+", "_", m).strip("_")[:30] or "m"
    return m.strip(), alias


def _overlap(a: dict, b: dict) -> bool:
    return (a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"]
            and a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"])


class WriteTools:
    def __init__(self, client: LuxmsClient, read):
        self.c, self.read = client, read

    # ---------- helpers ----------
    def _send(self, dry_run: bool, method: str, path: str, body: Any) -> Any:
        if dry_run:
            return {"dry_run": True, "would_send": {"method": method, "path": path, "body": body}}
        return self.c.request(method, path, json_body=body, write=True)

    def _dashlet(self, schema: str, dashlet_id: int, force: bool) -> dict:
        hits = [d for d in self.read.raw_dashlets(schema) if d.get("id") == dashlet_id]
        if not hits:
            raise LuxmsError(f"404 — в атласе {schema} нет дашлета {dashlet_id}")
        if not force and not _ours(hits[0]):
            raise LuxmsError("Отказ: дашлет создан не через MCP. Нужен force=true.")
        return hits[0]

    def _board(self, s: str, dashboard_id: int, force: bool) -> tuple[dict, list[dict]]:
        board = self.read.raw_dashboard(s, dashboard_id)
        if not force and not _ours(board):
            raise LuxmsError("Отказ: дашборд создан не через MCP. Нужен force=true.")
        return board, self.read.raw_dashlets(s, dashboard_id)

    def _frame(self, dashlets: list[dict], x, y, w: int, h: int) -> dict:
        """Explicit x/y, or the first free slot top-to-bottom, left-to-right."""
        w = max(1, min(int(w), GRID_W))
        if x is not None and y is not None:
            return {"x": x, "y": y, "w": w, "h": h}
        taken = [f for d in dashlets
                 if isinstance(f := (d.get("config") or {}).get("frame"), dict)
                 and all(k in f for k in ("x", "y", "w", "h"))]
        for yy in range(0, 200):
            for xx in range(0, GRID_W - w + 1):
                cand = {"x": xx, "y": yy, "w": w, "h": h}
                if not any(_overlap(cand, t) for t in taken):
                    return cand
        return {"x": 0, "y": 200, "w": w, "h": h}

    def _fields(self, s: str, cube: str) -> dict[str, str]:
        return {f["name"]: f.get("type", "") for f in self.read.describe_cube(s, cube) if f.get("name")}

    def _check(self, s: str, cube: str, dimensions: list[str], measures: list[str]) -> None:
        fields = self._fields(s, cube)
        bad = []
        for d in dimensions:
            if d not in fields:
                bad.append(d)
        for m in measures:
            expr, _ = _measure(m)
            fm = _FN.match(expr)
            if fm:
                if fm.group(2) not in fields:
                    bad.append(fm.group(2))
                elif fields[fm.group(2)] == "AGGFN":
                    raise LuxmsError(f"«{fm.group(2)}» уже формула-мера: пиши «{fm.group(2)}», без {fm.group(1)}().")
            elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", expr):
                if expr not in fields:
                    bad.append(expr)
                elif fields[expr] != "AGGFN":
                    raise LuxmsError(f"«{expr}» — не мера, а поле ({fields[expr]}). "
                                     f"Нужен агрегат: sum({expr}), avg({expr}) или count({expr}).")
        if bad:
            raise LuxmsError(f"В кубе {cube} нет полей: {bad}. Смотри cube_overview/describe_cube.")

    def _panel_dims(self, dashlets: list[dict], cube: str) -> list[str]:
        dims: list[str] = []
        for d in dashlets:
            ds = (d.get("config") or {}).get("dataSource") or {}
            if d.get("view_class") == "VizelKoobControl" and ds.get("koob") == cube:
                dims += [x for x in ds.get("dimensions") or [] if x not in dims]
        return dims

    def _filters(self, filters: dict | None, react_to: list[str] | None,
                 dashlets: list[dict], cube: str) -> dict:
        out: dict[str, Any] = {d: True for d in
                               (react_to if react_to is not None else self._panel_dims(dashlets, cube))}
        out.update(filters or {})
        return out

    def _post_dashlet(self, s: str, dashboard_id: int, body: dict, dry_run: bool) -> Any:
        out = self._send(dry_run, "POST", f"/api/db/{s}.dashlets/", body)
        if dry_run:
            return out
        new = out[0] if isinstance(out, list) and out else out
        return {"id": new.get("id") if isinstance(new, dict) else None,
                "frame": body["config"].get("frame"),
                "url": self.read.dashboard_url(s, dashboard_id)}

    # ---------- dashboards ----------
    def create_dashboard(self, atlas: str, title: str, dry_run: bool = True) -> Any:
        """Create an empty dashboard in an atlas. Returns its id and url."""
        s = self.read.resolve_atlas(atlas)
        body = {"title": title, "topic_id": 0,
                "config": {"icon": "dashboard", "viewport": {"h": 8, "w": 12}, MARK: True}}
        out = self._send(dry_run, "POST", f"/api/db/{s}.dashboards/", body)
        if dry_run:
            return out
        new = out[0] if isinstance(out, list) and out else out
        if isinstance(new, dict) and new.get("id") is not None:
            return {"id": new["id"], "url": self.read.dashboard_url(s, new["id"])}
        return {"response": out}

    def add_chart(self, atlas: str, dashboard_id: int, chart_type: str, cube: str,
                  dimensions: list[str], measures: list[str], title: str,
                  filters: dict | None = None, react_to: list[str] | None = None,
                  measure_titles: dict[str, str] | None = None, sort: str = "desc",
                  x: int | None = None, y: int | None = None, w: int = 6, h: int = 7,
                  dry_run: bool = True, force: bool = False) -> Any:
        """Add a chart. chart_type: bar, column, line, area, spline, stacked, table, waterfall,
        funnel, treemap, gauge, radar, heatmap, pie — or a raw Luxms view_class.
        measures: 'profit' (formula) or 'sum(sales)', 'avg(discount)'; 'expr:alias' to name it.
        measure_titles: {alias or expr: 'Подпись'} for the legend.
        filters: fixed filters {"categoryname": ["=", "Женская обувь"]}.
        react_to: dimensions this chart follows from the dashboard filter panel
        (default: all dimensions of filter panels on the dashboard for the same cube).
        x/y omitted -> placed in the first free slot (grid is 12 wide). sort: desc/asc/none."""
        s = self.read.resolve_atlas(atlas)
        _, dashlets = self._board(s, dashboard_id, force)
        if not dimensions and chart_type not in ("gauge",):
            raise LuxmsError("Нужно хотя бы одно измерение")
        if not measures:
            raise LuxmsError("Нужна хотя бы одна мера")
        self._check(s, cube, dimensions, measures)
        pairs = [_measure(m) for m in measures]
        titles = measure_titles or {}
        style = {a: {"title": titles.get(a) or titles.get(e)} for e, a in pairs
                 if titles.get(a) or titles.get(e)}
        cfg: dict[str, Any] = {
            MARK: True,
            "frame": self._frame(dashlets, x, y, w, h),
            "dataSource": {"koob": cube, "xAxis": ";".join(dimensions[:1]),
                           "yAxis": "measures", "dimensions": dimensions,
                           "measures": [f"{e}:{a}" if e != a else e for e, a in pairs],
                           "filters": self._filters(filters, react_to, dashlets, cube)},
        }
        if style:
            cfg["dataSource"]["style"] = {"measures": style}
        if sort in ("desc", "asc"):
            cfg["display"] = {"sort": sort.upper(), "sortBy": pairs[0][1]}
        body = {"dashboard_id": dashboard_id, "view_class": VIEW_CLASS.get(chart_type, chart_type),
                "title": title, "config": cfg}
        return self._post_dashlet(s, dashboard_id, body, dry_run)

    def add_kpi(self, atlas: str, dashboard_id: int, cube: str, measure: str, title: str,
                filters: dict | None = None, react_to: list[str] | None = None,
                as_percent: bool = False, number_format: str | None = None,
                x: int | None = None, y: int | None = None,
                w: int = 3, h: int = 2, dry_run: bool = True, force: bool = False) -> Any:
        """Add a big-number KPI tile (one measure).
        as_percent=true for a ratio measure (0.097 -> '9.7%'): multiplies by 100 and adds '%'.
        number_format overrides the format: '# ##0' (default), '# ##0.0', '##0.0%'.
        Note: '%' in a Luxms format is only a suffix, it does not multiply."""
        s = self.read.resolve_atlas(atlas)
        _, dashlets = self._board(s, dashboard_id, force)
        self._check(s, cube, [], [measure])
        expr, alias = _measure(measure)
        if as_percent:
            expr = f"100*({expr})"
        number_format = number_format or ("##0.0%" if as_percent else "# ##0")
        cfg = {
            MARK: True,
            "frame": self._frame(dashlets, x, y, w, h),
            "display": {"format": number_format},
            "dataSource": {"koob": cube, "xAxis": "", "yAxis": "measures", "dimensions": [],
                           "measures": [f"{expr}:{alias}" if expr != alias else expr],
                           "filters": self._filters(filters, react_to, dashlets, cube),
                           "style": {"measures": {alias: {"format": number_format}}}},
        }
        body = {"dashboard_id": dashboard_id, "view_class": "text", "title": title, "config": cfg}
        return self._post_dashlet(s, dashboard_id, body, dry_run)

    def add_note(self, atlas: str, dashboard_id: int, heading: str, text: str = "",
                 x: int | None = None, y: int | None = None, w: int = 12, h: int = 1,
                 dry_run: bool = True, force: bool = False) -> Any:
        """Add a heading/text block: dashboard intro, a finding, a recommendation."""
        s = self.read.resolve_atlas(atlas)
        _, dashlets = self._board(s, dashboard_id, force)
        cfg = {MARK: True, "url": "res:CustomHTML.js", "name": heading, "text": text,
               "frame": self._frame(dashlets, x, y, w, h), "options": ["!TopBar"],
               "dataSource": {"style": {}}}
        body = {"dashboard_id": dashboard_id, "view_class": "internal", "title": None, "config": cfg}
        return self._post_dashlet(s, dashboard_id, body, dry_run)

    def add_filter_panel(self, atlas: str, dashboard_id: int, cube: str, dimensions: list[str],
                         x: int | None = None, y: int | None = None, w: int = 3, h: int = 6,
                         dry_run: bool = True, force: bool = False) -> Any:
        """Add a filter panel (selectors for `dimensions`). Add it BEFORE charts: charts and KPIs
        on the same cube added later follow these filters automatically."""
        s = self.read.resolve_atlas(atlas)
        _, dashlets = self._board(s, dashboard_id, force)
        if not dimensions:
            raise LuxmsError("Нужно хотя бы одно поле для фильтра")
        self._check(s, cube, dimensions, [])
        cfg = {MARK: True, "frame": self._frame(dashlets, x, y, w, h),
               "dataSource": {"koob": cube, "xAxis": ";".join(dimensions), "yAxis": "measures",
                              "dimensions": dimensions}}
        body = {"dashboard_id": dashboard_id, "view_class": "VizelKoobControl", "title": "",
                "config": cfg}
        return self._post_dashlet(s, dashboard_id, body, dry_run)

    # ---------- edit / delete ----------
    def update_dashlet(self, atlas: str, dashlet_id: int, patch: dict,
                       dry_run: bool = True, force: bool = False) -> Any:
        """Patch dashlet fields (title, view_class, config). `config` is merged one level deep
        into the current config, other fields are replaced."""
        s = self.read.resolve_atlas(atlas)
        cur = self._dashlet(s, dashlet_id, force)
        body = dict(patch)
        if isinstance(patch.get("config"), dict):
            body["config"] = (cur.get("config") or {}) | patch["config"]
        return self._send(dry_run, "PUT", f"/api/db/{s}.dashlets/.filter(id={dashlet_id})", body)

    def delete_dashlet(self, atlas: str, dashlet_id: int, confirm: bool = False,
                       dry_run: bool = True, force: bool = False) -> Any:
        """Delete a dashlet. Needs dry_run=false AND confirm=true."""
        s = self.read.resolve_atlas(atlas)
        self._dashlet(s, dashlet_id, force)
        if not dry_run and not confirm:
            raise LuxmsError("Удаление только с confirm=true")
        return self._send(dry_run, "DELETE", f"/api/db/{s}.dashlets/.filter(id={dashlet_id})", None)

    def delete_dashboard(self, atlas: str, dashboard_id: int, confirm: bool = False,
                         dry_run: bool = True, force: bool = False) -> Any:
        """Delete a dashboard together with its dashlets. Needs dry_run=false AND confirm=true.
        Without force: only MCP-created dashboards with only MCP-created dashlets."""
        s = self.read.resolve_atlas(atlas)
        board = self.read.raw_dashboard(s, dashboard_id)
        dashlets = self.read.raw_dashlets(s, dashboard_id)
        if not force and (not _ours(board) or not all(_ours(d) for d in dashlets)):
            raise LuxmsError("Отказ: дашборд или его дашлеты созданы не через MCP. Нужен force=true.")
        if not dry_run and not confirm:
            raise LuxmsError("Удаление только с confirm=true")
        plan = [("DELETE", f"/api/db/{s}.dashlets/.filter(id={d['id']})") for d in dashlets]
        plan.append(("DELETE", f"/api/db/{s}.dashboards/.filter(id={dashboard_id})"))
        if dry_run:
            return {"dry_run": True, "would_send": [{"method": m, "path": p} for m, p in plan]}
        for m, p in plan:
            self.c.request(m, p, write=True)
        return {"deleted_dashboard": dashboard_id, "deleted_dashlets": [d["id"] for d in dashlets]}
