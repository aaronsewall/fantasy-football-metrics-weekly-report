from types import SimpleNamespace

from ffmwr.features.high_roller import HighRollerFeature


def response(html, status_code=200):
    return SimpleNamespace(text=html, status_code=status_code, url="https://spotrac.test")


def row(name="Rashee Rice", team='<img alt="KC">', amount="$1,000", date="09/15/23"):
    return f'''<tr><td><a class="link">{name}</a>{team}</td>
        <td class="text-left details-sm">WR</td>
        <td class="text-center details highlight">{amount}</td>
        <td class="text-right details">{date}</td>
        <td><span class="text-muted">- Late hit</span></td></tr>'''


def current_row(name="Houston Player", team="HOU", amount="$2,026", date="09/12/26"):
    return f'''<tr><td class="fines-player"><a>{name}</a></td>
        <td class="fines-position">CB</td><td class="fines-team">{team}</td>
        <td class="fines-infraction">- Unsportsmanlike conduct</td>
        <td class="fines-amount highlight">{amount}</td><td class="fines-date">{date}</td>
        <td class="fines-week">Week 1</td></tr>'''


def make_feature(monkeypatch, html, status_code=200, tmp_path=None):
    monkeypatch.setattr(
        "ffmwr.features.high_roller.requests.get", lambda *args, **kwargs: response(html, status_code)
    )
    return HighRollerFeature(2023, 1, tmp_path, refresh=True)


def test_valid_row_and_alternate_team_markup(monkeypatch, tmp_path):
    html = (
        "<table><tbody>"
        + row()
        + row("Trevor Lawrence", '<img src="/logos/jac.svg">', "$2,000")
        + "</tbody></table>"
    )
    feature = make_feature(monkeypatch, html, tmp_path=tmp_path)

    assert feature.get_player_fines_total("Rashee", "Rice", "KC", "WR") == 1000
    assert feature.get_player_fines_total("Trevor", "Lawrence", "JAX", "QB") == 2000


def test_malformed_row_does_not_discard_valid_data(monkeypatch, tmp_path):
    html = "<table><tbody>" + row(amount="not available") + row("Valid Player") + "</tbody></table>"
    feature = make_feature(monkeypatch, html, tmp_path=tmp_path)

    assert feature.get_player_fines_total("Valid", "Player", "KC", "WR") == 1000


def test_current_2026_fines_cells_parse_and_aggregate(monkeypatch, tmp_path):
    feature = make_feature(monkeypatch, "<table><tbody>" + current_row() + "</tbody></table>", tmp_path=tmp_path)

    assert feature.get_player_fines_total("Houston", "Player", "HOU", "CB") == 2026
    assert feature.feature_data["HOU"]["fines_count"] == 1


def test_blocked_or_missing_table_returns_team_zeroes(monkeypatch, tmp_path):
    blocked = make_feature(monkeypatch, "<html><title>Just a moment...</title></html>", tmp_path=tmp_path)
    missing = make_feature(monkeypatch, "<html>blocked</html>", 403, tmp_path)

    assert blocked.feature_data["KC"]["fines_total"] == 0.0
    assert missing.feature_data["KC"]["fines_count"] == 0


def test_aggregation_totals_and_worst_violation(monkeypatch, tmp_path):
    html = (
        "<table><tbody>"
        + row(amount="$1,000", date="09/15/23")
        + row(amount="$3,000", date="09/16/23")
        + "</tbody></table>"
    )
    feature = make_feature(monkeypatch, html, tmp_path=tmp_path)
    player = feature.feature_data[next(k for k in feature.feature_data if k.startswith("rashee"))]

    assert player["fines_count"] == 2
    assert player["fines_total"] == 4000
    assert player["worst_violation_fine"] == 3000
    assert feature.feature_data["KC"]["fines_total"] == 4000
