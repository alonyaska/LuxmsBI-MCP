"""Run against a stand: LUXMS_URL=... LUXMS_TOKEN=... uv run python tests/smoke_test.py [atlas]"""
import sys

from luxms_mcp.client import LuxmsClient, LuxmsError
from luxms_mcp.config import Config
from luxms_mcp.tools_read import ReadTools

atlas = sys.argv[1] if len(sys.argv) > 1 else "ds_demo171"
r = ReadTools(LuxmsClient(Config.from_env()))
ok = True


def check(name, fn):
    global ok
    try:
        out = fn()
        print(f"PASS {name}: {str(out)[:120]}")
    except LuxmsError as e:
        ok = False
        print(f"FAIL {name}: {e}")


check("ping", r.ping)
check("list_atlases", r.list_atlases)
check("list_cubes", lambda: r.list_cubes(atlas))
check("list_dashboards", lambda: r.list_dashboards(atlas))
check("get_dashboard", lambda: r.get_dashboard(atlas, 1))
CUBE = "luxmsbi._orders_full_2"
check("describe_cube", lambda: r.describe_cube(atlas, CUBE))
check("query_cube", lambda: r.query_cube(atlas, CUBE, ["cust_companyname", "profit"],
                                         {"categoryname": ["=", "Женская обувь"]}, ["-profit"], 3))
check("explain_query", lambda: r.explain_query(atlas, CUBE, ["cust_companyname", "profit"]))
check("count_rows", lambda: r.count_rows(atlas, CUBE, ["cust_companyname", "profit"]))
sys.exit(0 if ok else 1)
