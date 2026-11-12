"""Offline unit tests: no network. uv run pytest tests/test_offline.py"""
import pytest

from luxms_mcp.client import LuxmsError
from luxms_mcp.config import load_dotenv
from luxms_mcp.tools_analytics import AnalyticsTools
from luxms_mcp.tools_write import WriteTools, _measure

FIELDS = [
    {"name": "categoryname", "type": "STRING", "title": "Категория"},
    {"name": "country", "type": "STRING", "title": "Страна"},
    {"name": "orderdate", "type": "PERIOD", "title": "Дата"},
    {"name": "sales", "type": "SUM", "title": "Продажи"},
    {"name": "profit", "type": "AGGFN", "title": "Прибыль"},
    {"name": "discount", "type": "NUMBER", "title": "Скидка"},
    {"name": "orderid", "type": "NUMBER", "title": "id"},
]


class Cfg:
    max_rows = 200
    url = "https://stand"


class FakeClient:
    cfg = Cfg()

    def __init__(self):
        self.sent = []

    def request(self, method, path, json_body=None, write=False):
        self.sent.append((method, path, json_body))
        return {"id": 999}


class FakeRead:
    """Mimics ReadTools for one cube; query_cube answers from canned data."""

    def __init__(self, dashlets=None, ours=True, data=None):
        self.c = FakeClient()
        self.dashlets = dashlets or []
        self.ours = ours
        self.data = data or {}
        self.queries = []

    def resolve_atlas(self, atlas):
        return "ds_x"

    def describe_cube(self, s, cube, search=None):
        return FIELDS

    def raw_dashboard(self, s, did):
        return {"id": did, "config": {"_mcp": True} if self.ours else {}}

    def raw_dashlets(self, s, did=None):
        return self.dashlets

    def dashboard_url(self, s, did):
        return f"https://stand/#/{did}"

    def query_cube(self, s, cube, cols, filters=None, sort=None, limit=None):
        self.queries.append((cols, filters, sort))
        key = (tuple(cols), str((filters or {}).get("orderdate")))
        rows = self.data.get(key, self.data.get(tuple(cols), []))
        return {"rows": rows, "truncated": False}


# ---------- measures ----------
@pytest.mark.parametrize("src,expr,alias", [
    ("profit", "profit", "profit"),
    ("sum(sales)", "sum(sales)", "sum_sales"),
    ("avg(discount)", "avg(discount)", "avg_discount"),
    ("count(distinct orderid)", "count(distinct orderid)", "count_orderid"),
    ("sum(sales):s", "sum(sales)", "s"),
    ("100*sum(a)/sum(b)", "100*sum(a)/sum(b)", "100_sum_a_sum_b"),
])
def test_measure_alias(src, expr, alias):
    assert _measure(src) == (expr, alias)


# ---------- layout ----------
def frame(x, y, w, h):
    return {"config": {"frame": {"x": x, "y": y, "w": w, "h": h}}}


def test_frame_first_slot_empty_board():
    w = WriteTools(FakeClient(), FakeRead())
    assert w._frame([], None, None, 6, 7) == {"x": 0, "y": 0, "w": 6, "h": 7}


def test_frame_next_to_and_below():
    w = WriteTools(FakeClient(), FakeRead())
    assert w._frame([frame(0, 0, 6, 7)], None, None, 6, 7) == {"x": 6, "y": 0, "w": 6, "h": 7}
    full = [frame(0, 0, 6, 7), frame(6, 0, 6, 7)]
    assert w._frame(full, None, None, 6, 7)["y"] == 7
    assert w._frame([frame(0, 0, 12, 1)], None, None, 3, 2) == {"x": 0, "y": 1, "w": 3, "h": 2}


def test_frame_explicit_and_clamped():
    w = WriteTools(FakeClient(), FakeRead())
    assert w._frame([frame(0, 0, 12, 12)], 2, 3, 99, 4) == {"x": 2, "y": 3, "w": 12, "h": 4}


# ---------- validation ----------
def test_check_rejects_unknown_and_wrong_aggregation():
    w = WriteTools(FakeClient(), FakeRead())
    w._check("ds_x", "c.k", ["categoryname"], ["profit", "sum(sales)", "avg(discount)"])
    with pytest.raises(LuxmsError, match="нет полей"):
        w._check("ds_x", "c.k", ["nope"], [])
    with pytest.raises(LuxmsError, match="без sum"):
        w._check("ds_x", "c.k", [], ["sum(profit)"])
    with pytest.raises(LuxmsError, match="Нужен агрегат"):
        w._check("ds_x", "c.k", [], ["sales"])


# ---------- write tools build the right bodies ----------
def test_add_chart_body_and_auto_react_to_panel():
    panel = {"view_class": "VizelKoobControl",
             "config": {"_mcp": True, "frame": {"x": 0, "y": 0, "w": 3, "h": 6},
                        "dataSource": {"koob": "c.k", "dimensions": ["country", "orderdate"]}}}
    read = FakeRead(dashlets=[panel])
    out = WriteTools(read.c, read).add_chart(
        "a", 1, "bar", "c.k", ["categoryname"], ["sum(sales)", "profit"], "t",
        filters={"categoryname": ["!=", "X"]}, measure_titles={"sum_sales": "Продажи"})
    body = out["would_send"]["body"]
    ds = body["config"]["dataSource"]
    assert body["view_class"] == "1II.bar" and body["config"]["_mcp"] is True
    assert ds["measures"] == ["sum(sales):sum_sales", "profit"]
    assert ds["filters"] == {"country": True, "orderdate": True, "categoryname": ["!=", "X"]}
    assert ds["style"] == {"measures": {"sum_sales": {"title": "Продажи"}}}
    assert body["config"]["display"] == {"sort": "DESC", "sortBy": "sum_sales"}
    assert body["config"]["frame"] == {"x": 3, "y": 0, "w": 6, "h": 7}


def test_add_kpi_note_panel_bodies():
    read = FakeRead()
    w = WriteTools(read.c, read)
    kpi = w.add_kpi("a", 1, "c.k", "sum(sales)", "Выручка")["would_send"]["body"]
    assert kpi["view_class"] == "text"
    assert kpi["config"]["dataSource"]["measures"] == ["sum(sales):sum_sales"]
    assert kpi["config"]["display"] == {"format": "# ##0"}
    pct = w.add_kpi("a", 1, "c.k", "profit", "Маржа", as_percent=True)["would_send"]["body"]
    assert pct["config"]["dataSource"]["measures"] == ["100*(profit):profit"]
    assert pct["config"]["display"] == {"format": "##0.0%"}
    note = w.add_note("a", 1, "Вывод", "Скидки съедают маржу")["would_send"]["body"]
    assert note["view_class"] == "internal" and note["config"]["url"] == "res:CustomHTML.js"
    assert note["config"]["name"] == "Вывод" and note["config"]["frame"]["w"] == 12
    panel = w.add_filter_panel("a", 1, "c.k", ["country", "orderdate"])["would_send"]["body"]
    assert panel["view_class"] == "VizelKoobControl"
    assert panel["config"]["dataSource"]["xAxis"] == "country;orderdate"


def test_guards():
    read = FakeRead(ours=False)
    w = WriteTools(read.c, read)
    with pytest.raises(LuxmsError, match="force"):
        w.add_chart("a", 1, "bar", "c.k", ["country"], ["profit"], "t")
    with pytest.raises(LuxmsError, match="force"):
        w.add_note("a", 1, "x")
    assert w.add_note("a", 1, "x", force=True)["dry_run"] is True
    read2 = FakeRead(dashlets=[{"id": 5, "config": {"_mcp": True}}])
    with pytest.raises(LuxmsError, match="confirm"):
        WriteTools(read2.c, read2).delete_dashboard("a", 1, dry_run=False)
    assert read2.c.sent == []


def test_real_send_only_when_dry_run_false():
    read = FakeRead()
    w = WriteTools(read.c, read)
    w.add_kpi("a", 1, "c.k", "profit", "П")
    assert read.c.sent == []
    out = w.add_kpi("a", 1, "c.k", "profit", "П", dry_run=False)
    assert out["id"] == 999 and read.c.sent[0][0:2] == ("POST", "/api/db/ds_x.dashlets/")


# ---------- analytics ----------
def test_breakdown_shares_and_others():
    data = {("categoryname", "sum(sales):m"): [{"categoryname": "A", "m": 60}, {"categoryname": "B", "m": 30},
                                                {"categoryname": "C", "m": 10}],
            ("sum(sales):m",): [{"m": 100}]}
    out = AnalyticsTools(FakeRead(data=data)).breakdown("a", "c.k", "categoryname", "sum(sales)", top=2)
    assert out["total"] == 100
    assert out["rows"][0] == {"categoryname": "A", "value": 60, "share_pct": 60.0, "cum_share_pct": 60.0}
    assert out["rows"][1]["cum_share_pct"] == 90.0
    assert out["others"] == {"value": 10, "share_pct": 10.0}


def test_breakdown_formula_has_no_shares():
    data = {("country", "profit:m"): [{"country": "A", "m": 5}], ("profit:m",): [{"m": 5}]}
    out = AnalyticsTools(FakeRead(data=data)).breakdown("a", "c.k", "country", "profit")
    assert "share_pct" not in out["rows"][0] and "note" in out


def test_compare_periods_sorted_by_abs_delta():
    pa, pb = ["2005-01-01", "2005-12-31"], ["2006-01-01", "2006-12-31"]
    cols = ("categoryname", "sum(sales):m")
    data = {(cols, str(["between", *pa])): [{"categoryname": "A", "m": 100}, {"categoryname": "B", "m": 50}],
            (cols, str(["between", *pb])): [{"categoryname": "A", "m": 90}, {"categoryname": "B", "m": 150},
                                            {"categoryname": "C", "m": 5}]}
    out = AnalyticsTools(FakeRead(data=data)).compare_periods(
        "a", "c.k", "orderdate", pa, pb, "sum(sales)", "categoryname")
    assert [r["categoryname"] for r in out["rows"]] == ["B", "A", "C"]
    assert out["rows"][0] == {"categoryname": "B", "a": 50, "b": 150, "delta": 100, "delta_pct": 200.0}
    assert out["rows"][2]["delta_pct"] is None  # new in period B
    assert out["total"] == {"a": 150, "b": 245, "delta": 95, "delta_pct": 63.33}


def test_compare_periods_validates_period():
    with pytest.raises(LuxmsError):
        AnalyticsTools(FakeRead()).compare_periods("a", "c.k", "orderdate", ["2005"], ["a", "b"], "profit")


# ---------- .env parsing ----------
def test_dotenv_strips_comments_and_quotes(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "LX_A=BI-User-Session=abc-123# comment here\n"
        "LX_B='quoted # kept'\n"
        "# LX_C=skip\n"
        "export LX_D=1\n"
        "LX_E=preset\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    for k in ("LX_A", "LX_B", "LX_C", "LX_D"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("LX_E", "env-wins")
    monkeypatch.setattr("luxms_mcp.config.PROJECT_ENV", tmp_path / "none")
    load_dotenv()
    import os
    assert os.environ["LX_A"] == "BI-User-Session=abc-123"
    assert os.environ["LX_B"] == "quoted # kept"
    assert "LX_C" not in os.environ
    assert os.environ["LX_D"] == "1"
    assert os.environ["LX_E"] == "env-wins"
