from bypasshub.gui import theme as T
from bypasshub.gui.surface import build_mapping, gradient_column


def test_palette_tint_colors_background():
    neutral = T.make_palette({"mode": "dark", "colors": ["#ff0000"], "tint": 0})
    tinted = T.make_palette({"mode": "dark", "colors": ["#ff0000"], "tint": 100})
    assert neutral.bg == "#0f1115"
    r, g, b = T.hex_to_rgb(tinted.bg)
    assert r > g and r > b  # фон окрасился в красный


def test_palette_gradient_stops_follow_colors():
    p = T.make_palette({"mode": "light", "colors": ["#ff0000", "#0000ff"], "tint": 80})
    assert len(p.page_stops) == 2 and len(p.side_stops) == 2
    top, bottom = T.hex_to_rgb(p.page_stops[0]), T.hex_to_rgb(p.page_stops[1])
    assert top[0] > top[2] and bottom[2] > bottom[0]


def test_roles_are_distinct_for_recolor():
    for mode in ("dark", "light"):
        p = T.make_palette({"mode": mode, "colors": ["#2b7fff", "#00c6ff"]})
        roles = p.roles()
        assert all(roles.values())
        # кнопки (on_accent) и ползунки (knob) не должны совпадать — иначе перекраска их спутает
        assert roles["knob"].lower() != roles["on_accent"].lower()


def test_build_mapping():
    a = T.make_palette({"mode": "dark", "colors": ["#2b7fff"]})
    b = T.make_palette({"mode": "light", "colors": ["#ff5f6d"]})
    m = build_mapping(a, b)
    assert m[a.text.lower()] == b.text
    assert m[a.accent.lower()] == b.accent


def test_gradient_column_ping_pong():
    col = gradient_column(["#000000", "#ffffff"], 21, 10)
    assert col[0] == "#000000" and col[10] == "#ffffff" and col[20] == "#000000"


def test_gradient_at_bounds():
    assert T.gradient_at(["#000000", "#ffffff"], -1) == "#000000"
    assert T.gradient_at(["#000000", "#ffffff"], 2) == "#ffffff"
    assert T.gradient_at(["#123456"], 0.5) == "#123456"


def test_remap_nested_corner_colors():
    from bypasshub.gui.surface import _remap
    m = {"#111111": "#222222"}
    assert _remap(("#111111", ["#111111", "#111111"], "#333333"), m) == \
        ("#222222", ["#222222", "#222222"], "#333333")
    assert _remap(("#333333",), m) is None
    assert _remap("#111111", m) == "#222222"
