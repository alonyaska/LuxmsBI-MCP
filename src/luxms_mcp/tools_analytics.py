"""Analytics helpers on top of the cube data API: overview, breakdown (Pareto), period compare.

Measure syntax verified on hackathon20 (ds_demo171):
  - SUM fields need an aggregate: "sum(sales)"; result key is the field name ("sales").
  - AGGFN fields are formulas already: "profit" as is ("sum(profit)" -> HTTP 500).
  - Any NUMBER field can be aggregated: "avg(discount)", "count(orderid)".
  - "expr:alias" renames the result key; sort by result key: "-sales".
  - Filters: {"dim": ["=", v1, v2]}, ["!=", v], ["between", from, to] (dates as 'YYYY-MM-DD').
"""
import re
from typing import Any

from .client import LuxmsError
from .tools_read import ReadTools

ALIAS = "m"
_ADDITIVE = re.compile(r"^\s*(sum|count)\s*\(", re.I)


def _num(v: Any) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _round(v: float | None, nd: int = 2) -> float | None:
    return None if v is None else round(v, nd)


def _pct(part: float | None, whole: float | None) -> float | None:
    if part is None or not whole:
        return None
    return round(100.0 * part / whole, 2)


class AnalyticsTools:
    def __init__(self, read: ReadTools):
        self.r = read

    # ---------- overview ----------
    def cube_overview(self, atlas: str, cube: str) -> dict:
        """One-call map of a cube: what to group by, what to measure, date ranges."""
        s = self.r.resolve_atlas(atlas)
        fields = self.r.describe_cube(s, cube)
        out: dict[str, list] = {"dimensions": [], "dates": [], "measures": [], "numeric": []}
        for f in fields:
            name, title, typ = f.get("name"), f.get("title"), f.get("type")
            if typ == "STRING":
                out["dimensions"].append({"name": name, "title": title})
            elif typ == "PERIOD":
                out["dates"].append({"name": name, "title": title})
            elif typ == "AGGFN":
                out["measures"].append({"expr": name, "title": title, "kind": "formula"})
            elif typ == "SUM":
                out["measures"].append({"expr": f"sum({name})", "title": title, "kind": "sum"})
            elif typ == "NUMBER" and not re.search(r"(^|_)id$|id$", name or ""):
                out["numeric"].append({"name": name, "title": title, "use_as": f"avg({name})"})
        for d in out["dates"]:
            try:
                row = self.r.query_cube(s, cube, [f"min({d['name']}):lo", f"max({d['name']}):hi"])["rows"]
                if row:
                    d["from"], d["to"] = row[0].get("lo"), row[0].get("hi")
            except LuxmsError:
                pass
        out["hint"] = ("measures[].expr goes straight into columns/measure; numeric fields need an "
                       "aggregate (avg/sum/min/max/count). Dates filter as ['between','YYYY-MM-DD','YYYY-MM-DD'].")
        return {"atlas": s, "cube": cube} | out

    # ---------- breakdown ----------
    def _total(self, s: str, cube: str, measure: str, filters: dict | None) -> float | None:
        rows = self.r.query_cube(s, cube, [f"{measure}:{ALIAS}"], filters)["rows"]
        return _num(rows[0].get(ALIAS)) if rows else None

    def breakdown(self, atlas: str, cube: str, dimension: str, measure: str,
                  filters: dict | None = None, top: int = 10, ascending: bool = False) -> dict:
        """Top-N of `dimension` by `measure` with share of total and cumulative share (Pareto).
        Share is only computed for additive measures (sum(...)/count(...))."""
        s = self.r.resolve_atlas(atlas)
        top = max(1, min(top, self.r.c.cfg.max_rows))
        cols = [dimension, f"{measure}:{ALIAS}"]
        res = self.r.query_cube(s, cube, cols, filters, [("" if ascending else "-") + ALIAS],
                                limit=self.r.c.cfg.max_rows)
        rows = res["rows"]
        additive = bool(_ADDITIVE.match(measure))
        total = self._total(s, cube, measure, filters)
        items, cum = [], 0.0
        for row in rows[:top]:
            v = _num(row.get(ALIAS))
            item = {dimension: row.get(dimension), "value": _round(v)}
            if additive:
                cum += v or 0.0
                item["share_pct"] = _pct(v, total)
                item["cum_share_pct"] = _pct(cum, total)
            items.append(item)
        out: dict[str, Any] = {"dimension": dimension, "measure": measure, "total": _round(total),
                               "groups": len(rows) if not res["truncated"] else f">{len(rows)}",
                               "rows": items}
        if additive and total and len(rows) > top:
            rest = (total or 0) - cum
            out["others"] = {"value": _round(rest), "share_pct": _pct(rest, total)}
        if not additive:
            out["note"] = "Мера не аддитивная (формула/avg): доли от итога не считаются."
        return out

    # ---------- compare periods ----------
    def compare_periods(self, atlas: str, cube: str, date_field: str,
                        period_a: list[str], period_b: list[str], measure: str,
                        dimension: str | None = None, filters: dict | None = None,
                        top: int = 10) -> dict:
        """Measure in period A vs period B, overall or per `dimension`; rows sorted by the
        absolute change, so the biggest drivers of growth/decline come first.
        period_* = ['YYYY-MM-DD', 'YYYY-MM-DD']."""
        s = self.r.resolve_atlas(atlas)
        for p in (period_a, period_b):
            if not (isinstance(p, list) and len(p) == 2):
                raise LuxmsError("Период задаётся как ['YYYY-MM-DD', 'YYYY-MM-DD']")

        def run(period: list[str]) -> dict:
            f = dict(filters or {}) | {date_field: ["between", period[0], period[1]]}
            cols = ([dimension] if dimension else []) + [f"{measure}:{ALIAS}"]
            rows = self.r.query_cube(s, cube, cols, f, limit=self.r.c.cfg.max_rows)["rows"]
            return {(r.get(dimension) if dimension else "__all__"): _num(r.get(ALIAS)) for r in rows}

        a, b = run(period_a), run(period_b)
        keys = list(dict.fromkeys(list(a) + list(b)))
        items = []
        for k in keys:
            va, vb = a.get(k), b.get(k)
            delta = None if va is None and vb is None else (vb or 0.0) - (va or 0.0)
            items.append({**({dimension: k} if dimension else {}),
                          "a": _round(va), "b": _round(vb), "delta": _round(delta),
                          "delta_pct": _pct(delta, va) if va else None})
        items.sort(key=lambda x: abs(x["delta"] or 0), reverse=True)
        out: dict[str, Any] = {"measure": measure, "period_a": period_a, "period_b": period_b,
                               "rows": items[:top] if dimension else items}
        if dimension:
            ta, tb = sum(v or 0 for v in a.values()), sum(v or 0 for v in b.values())
            if _ADDITIVE.match(measure):
                out["total"] = {"a": _round(ta), "b": _round(tb), "delta": _round(tb - ta),
                                "delta_pct": _pct(tb - ta, ta)}
        return out
