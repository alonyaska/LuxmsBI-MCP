"""End-to-end through a real MCP stdio client: the demo scenario.
Creates '[mcp] test e2e' on the stand and deletes it at the end.
    uv run python tests/e2e_mcp.py
"""
import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ATLAS, CUBE = "Fashion Evolution", "luxmsbi._orders_full_2"


def show(name, res):
    parts = [getattr(c, "text", "") for c in res.content]
    vals = []
    for p in parts:
        try:
            vals.append(json.loads(p))
        except ValueError:
            vals.append(p)
    out = vals[0] if len(vals) == 1 else vals
    print(f"\n### {name}{' [ERROR]' if res.isError else ''}\n"
          f"{json.dumps(out, ensure_ascii=False)[:500]}")
    return out


async def main():
    env = dict(os.environ, LUXMS_READONLY="0")
    params = StdioServerParameters(command=sys.executable, args=["-m", "luxms_mcp.server"], env=env)
    async with stdio_client(params) as (rd, wr), ClientSession(rd, wr) as s:
        await s.initialize()
        tools = [t.name for t in (await s.list_tools()).tools]
        print("tools:", tools)
        call = lambda n, **a: s.call_tool(n, a)
        show("ping", await call("ping"))
        show("list_atlases", await call("list_atlases"))
        show("list_cubes", await call("list_cubes", atlas=ATLAS, search="orders_full_2"))
        show("describe_cube", await call("describe_cube", atlas=ATLAS, cube=CUBE, search="country"))
        show("query_cube", await call("query_cube", atlas=ATLAS, cube=CUBE,
                                      columns=["country", "profit"], sort=["-profit"], limit=5))
        board = show("create_dashboard", await call("create_dashboard", atlas=ATLAS,
                                                    title="[mcp] test e2e", dry_run=False))
        bid = board["id"]
        try:
            show("add_chart", await call("add_chart", atlas=ATLAS, dashboard_id=bid, chart_type="column",
                                         cube=CUBE, dimensions=["country"], measures=["profit"],
                                         title="[mcp] test прибыль по странам", dry_run=False))
            dash = show("get_dashboard", await call("get_dashboard", atlas=ATLAS, dashboard_id=bid))
            did = dash["dashlets"][0]["id"]
            show("update_dashlet", await call("update_dashlet", atlas=ATLAS, dashlet_id=did,
                                              patch={"title": "[mcp] test (updated)"}, dry_run=False))
            show("guard: foreign dashboard", await call("add_chart", atlas=ATLAS, dashboard_id=1,
                                                         chart_type="bar", cube=CUBE, dimensions=["country"],
                                                         measures=["profit"], title="x", dry_run=False))
            show("guard: no confirm", await call("delete_dashboard", atlas=ATLAS, dashboard_id=bid,
                                                  dry_run=False))
        finally:
            show("delete_dashboard", await call("delete_dashboard", atlas=ATLAS, dashboard_id=bid,
                                                 dry_run=False, confirm=True))
        left = show("list_dashboards", await call("list_dashboards", atlas=ATLAS))
        print("\nCLEAN:", not any("[mcp] test" in (b.get("title") or "") for b in left))


asyncio.run(main())
