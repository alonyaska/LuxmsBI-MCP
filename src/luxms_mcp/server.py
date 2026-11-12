import logging
import sys

from mcp.server.fastmcp import FastMCP

from .client import LuxmsClient, LuxmsError
from .config import Config
from .tools_analytics import AnalyticsTools
from .tools_read import ReadTools
from .tools_write import WriteTools


def _safe(fn):
    """Turn LuxmsError into a readable tool error."""
    import functools

    @functools.wraps(fn)
    def w(*a, **kw):
        try:
            return fn(*a, **kw)
        except LuxmsError as e:
            raise ValueError(str(e)) from None
    return w


def build() -> FastMCP:
    cfg = Config.from_env()
    logging.basicConfig(filename="luxms-mcp.log", level=logging.INFO,
                        format="%(asctime)s %(message)s")
    client = LuxmsClient(cfg)
    r = ReadTools(client)
    mcp = FastMCP("luxms-bi")

    @mcp.tool()
    @_safe
    def ping() -> dict:
        """Show Luxms stand URL, auth mode and whether write tools are enabled."""
        return r.ping()

    @mcp.tool()
    @_safe
    def list_atlases(search: str | None = None) -> list[dict]:
        """List atlases (dashboard folders). `search` filters by substring."""
        return r.list_atlases(search)

    @mcp.tool()
    @_safe
    def list_dashboards(atlas: str) -> list[dict]:
        """List dashboards of an atlas. `atlas` = schema (ds_demo171) or title."""
        return r.list_dashboards(atlas)

    @mcp.tool()
    @_safe
    def get_dashboard(atlas: str, dashboard_id: int, raw_config: bool = False) -> dict:
        """Dashboard with its dashlets (view_class, cube, dimensions, measures, set filters).
        raw_config=true returns full dashlet configs (large) — use as a template for add_chart."""
        return r.get_dashboard(atlas, dashboard_id, raw_config)

    @mcp.tool()
    @_safe
    def list_cubes(atlas: str, search: str | None = None, include_global: bool = False) -> list[dict]:
        """Cubes of an atlas; `cube` field ('source.name') is what other tools take."""
        return r.list_cubes(atlas, search, include_global)

    @mcp.tool()
    @_safe
    def describe_cube(atlas: str, cube: str, search: str | None = None) -> list[dict]:
        """Dimensions and measures of a cube (id, title, type)."""
        return r.describe_cube(atlas, cube, search)

    _q = ("cube: 'luxmsbi._orders_full_2' (start with cube_overview). columns: dimensions as is, "
          "measures as cube_overview gives them: formulas as is ('profit'), SUM fields wrapped "
          "('sum(sales)'), any number aggregated ('avg(discount)', 'count(orderid)'); "
          "'expr:alias' renames a result key. filters: {\"country\": [\"=\", \"USA\", \"BRA\"]}, "
          "[\"!=\", v], dates [\"between\", \"2006-01-01\", \"2006-12-31\"]. "
          "sort by result key: [\"-sales\"].")

    @mcp.tool(description="Query cube data (capped by LUXMS_MAX_ROWS). " + _q)
    @_safe
    def query_cube(atlas: str, cube: str, columns: list[str], filters: dict | None = None,
                   sort: list[str] | None = None, limit: int | None = None) -> dict:
        return r.query_cube(atlas, cube, columns, filters, sort, limit)

    @mcp.tool(description="Return the SQL Luxms would run for a cube query. " + _q)
    @_safe
    def explain_query(atlas: str, cube: str, columns: list[str], filters: dict | None = None,
                      sort: list[str] | None = None):
        return r.explain_query(atlas, cube, columns, filters, sort)

    @mcp.tool(description="Count rows of a cube query. " + _q)
    @_safe
    def count_rows(atlas: str, cube: str, columns: list[str], filters: dict | None = None):
        return r.count_rows(atlas, cube, columns, filters)

    a = AnalyticsTools(r)
    for fn in (a.cube_overview, a.breakdown, a.compare_periods):
        mcp.tool()(_safe(fn))

    if not cfg.readonly:
        w = WriteTools(client, r)
        for fn in (w.create_dashboard, w.add_filter_panel, w.add_kpi, w.add_chart, w.add_note,
                   w.update_dashlet, w.delete_dashlet, w.delete_dashboard):
            mcp.tool()(_safe(fn))
    return mcp


def main() -> None:
    try:
        build().run()  # stdio
    except RuntimeError as e:
        print(f"luxms-mcp: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
