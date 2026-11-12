"""Demo scenario through a real MCP stdio client: data -> hypothesis -> dashboard.

    uv run python tests/demo_scenario.py            # build, print url, delete at the end
    uv run python tests/demo_scenario.py --keep     # leave the dashboard on the stand to look at
    uv run python tests/demo_scenario.py --delete N # delete a kept dashboard N

Everything it creates is titled "[mcp] test ..." and flagged _mcp. Atlas/cube via env:
DEMO_ATLAS (default 'Fashion Evolution'), DEMO_CUBE (default 'luxmsbi._orders_full_2').
"""
import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ATLAS = os.getenv("DEMO_ATLAS", "Fashion Evolution")
CUBE = os.getenv("DEMO_CUBE", "luxmsbi._orders_full_2")
FAILS: list[str] = []


def parse(res):
    vals = []
    for c in res.content:
        t = getattr(c, "text", "")
        try:
            vals.append(json.loads(t))
        except ValueError:
            vals.append(t)
    return vals[0] if len(vals) == 1 else vals


async def main():
    keep = "--keep" in sys.argv
    env = dict(os.environ, LUXMS_READONLY="0")
    params = StdioServerParameters(command=sys.executable, args=["-m", "luxms_mcp.server"], env=env)
    async with stdio_client(params) as (rd, wr), ClientSession(rd, wr) as s:
        await s.initialize()

        async def call(name, expect_error=False, **args):
            res = await s.call_tool(name, args)
            out = parse(res)
            ok = res.isError == expect_error
            if not ok:
                FAILS.append(name)
            print(f"\n[{'ok' if ok else 'FAIL'}] {name}"
                  f"{' (ожидаемый отказ)' if expect_error else ''}\n  "
                  f"{json.dumps(out, ensure_ascii=False)[:420]}")
            return out

        if "--delete" in sys.argv:
            bid = int(sys.argv[sys.argv.index("--delete") + 1])
            await call("delete_dashboard", atlas=ATLAS, dashboard_id=bid, dry_run=False, confirm=True)
            return

        # 1. explore
        ov = await call("cube_overview", atlas=ATLAS, cube=CUBE)
        date = next((d["name"] for d in ov["dates"] if d["name"] == "orderdate"), ov["dates"][0]["name"])
        # 2. hypotheses
        await call("breakdown", atlas=ATLAS, cube=CUBE, dimension="categoryname",
                   measure="sum(sales)", top=5)
        await call("breakdown", atlas=ATLAS, cube=CUBE, dimension="cust_country",
                   measure="profit", top=5, ascending=True)
        await call("compare_periods", atlas=ATLAS, cube=CUBE, date_field=date,
                   period_a=["2005-01-01", "2005-12-31"], period_b=["2006-01-01", "2006-12-31"],
                   measure="sum(sales)", dimension="categoryname", top=5)
        await call("query_cube", atlas=ATLAS, cube=CUBE,
                   columns=["categoryname", "avg(discount):disc", "profit_percent"], sort=["-disc"])
        # validation errors are readable, nothing is sent
        await call("query_cube", expect_error=True, atlas=ATLAS, cube=CUBE,
                   columns=["categoryname", "sum(profit)"])

        # 3. dashboard
        board = await call("create_dashboard", atlas=ATLAS, title="[mcp] test demo: скидки и прибыль",
                           dry_run=False)
        bid = board["id"]
        try:
            await call("add_note", atlas=ATLAS, dashboard_id=bid, dry_run=False,
                       heading="Где теряем прибыль",
                       text="Продажи растут, а прибыль по странам и категориям сильно разная. "
                            "Смотрим, где скидки съедают маржу.")
            await call("add_filter_panel", atlas=ATLAS, dashboard_id=bid, cube=CUBE,
                       dimensions=["cust_country", "categoryname"], y=1, x=9, w=3, h=9,
                       dry_run=False)
            await call("add_kpi", atlas=ATLAS, dashboard_id=bid, cube=CUBE, measure="sum(sales)",
                       title="Выручка", dry_run=False)
            await call("add_kpi", atlas=ATLAS, dashboard_id=bid, cube=CUBE, measure="profit",
                       title="Прибыль", dry_run=False)
            await call("add_kpi", atlas=ATLAS, dashboard_id=bid, cube=CUBE, measure="profit_percent",
                       title="Маржа", as_percent=True, dry_run=False)
            await call("add_chart", atlas=ATLAS, dashboard_id=bid, chart_type="bar", cube=CUBE,
                       dimensions=["cust_country"], measures=["profit"], w=5,
                       title="Прибыль по странам", measure_titles={"profit": "Прибыль"}, dry_run=False)
            await call("add_chart", atlas=ATLAS, dashboard_id=bid, chart_type="column", cube=CUBE,
                       dimensions=["categoryname"], measures=["avg(discount)"], w=4,
                       title="Средняя скидка по категориям",
                       measure_titles={"avg_discount": "Скидка"}, dry_run=False)
            # guards
            await call("add_chart", expect_error=True, atlas=ATLAS, dashboard_id=bid, chart_type="bar",
                       cube=CUBE, dimensions=["cust_country"], measures=["sales"], title="x")
            await call("add_chart", expect_error=True, atlas=ATLAS, dashboard_id=1, chart_type="bar",
                       cube=CUBE, dimensions=["cust_country"], measures=["profit"], title="x",
                       dry_run=False)
            dash = await call("get_dashboard", atlas=ATLAS, dashboard_id=bid)
            print("\nLAYOUT:", [(d["view_class"], d["frame"]) for d in dash["dashlets"]])
            print("\nURL:", dash["url"])
            print("DASHBOARD_ID:", bid)
        finally:
            if not keep:
                await call("delete_dashboard", atlas=ATLAS, dashboard_id=bid, dry_run=False, confirm=True)
    print("\nИТОГ:", "всё ок" if not FAILS else f"ошибки в {FAILS}")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
