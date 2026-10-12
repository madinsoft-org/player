import json
from pathlib import Path

import pytest

from src_new.extract_magic_rules import logical_lines, parse_magic, write_catalog


def deck(rules: str) -> str:
    return (
        "types\nwidth ignored 20\nend\ndrc\n"
        "style drc variants (fast),(full),(routing)\n"
        "scalefactor 50\nvariants (fast),(full)\n"
        + rules + "\nend\nextract\nwidth ignored 40\nend\n"
    )


def test_single_layer_and_pair_groups_preserve_expressions() -> None:
    catalog = parse_magic(deck(
        'width (a,b)/m1 230 "Width (CO.1)"\n'
        'spacing (a,b)/m1 (a,b)/m1 240 touching_ok "Space (CO.2)"\n'
        'spacing a b 100 corner_ok allfets "Spacing (PL.5a)"\n'
        'surround a *m1 55 35 directional "Enclosure (CO.6)"\n'
        'area *m1,obsm1 144400 230 "Area (M1.3)"'
    ), "gf180", "example.tech")
    assert catalog["extracted_count"] == 5
    assert len(catalog["single_layer"]["(a,b)/m1"]) == 2
    pairs = catalog["between_layers"]
    assert pairs[0]["layers"] == ["a", "b"]
    assert pairs[0]["rules"][0]["options"] == ["corner_ok", "allfets"]
    assert pairs[1]["layers"] == ["a", "*m1"]
    surround = pairs[1]["rules"][0]
    assert surround["values"] == {"distance": 55, "distance2": 35}
    assert surround["reference"] == "CO.6"
    assert surround["variants"] == ["fast", "full"]
    area = catalog["single_layer"]["*m1,obsm1"][0]
    assert area["values"] == {"min_area": 144400, "min_edge": 230}
    assert catalog["units"]["distance"] == "drc_unit"


def test_variants_style_reset_and_direction_are_retained() -> None:
    catalog = parse_magic(deck(
        'variants (full)\nspacing a b 100 touching_ok "First"\n'
        'variants *\nspacing b a 200 touching_ok "Second"\n'
        'style empty\nscalefactor 50'
    ), "gf180", "example.tech")
    first, second = catalog["between_layers"]
    assert first["rules"][0]["variants"] == ["full"]
    assert second["rules"][0]["variants"] == ["*"]
    assert second["layers"] == ["b", "a"]
    assert catalog["styles"]["empty"]["variants"] == ["*"]


def test_continuations_comments_and_source_lines() -> None:
    text = deck(
        '# width ignored 1 "Comment"\n'
        'width a \\\n  150 "# in message (a.1)" # outside\n'
        'cifwidth derived 120 "Not an explicit width"'
    )
    catalog = parse_magic(text, "sky130", "test.tech")
    rule = catalog["single_layer"]["a"][0]
    assert rule["line"] == 9
    assert rule["values"]["distance"] == 150
    assert catalog["omitted_commands"] == {"cifwidth": 1}


def test_fractional_scale_and_width_angles(tmp_path: Path) -> None:
    text = deck('width nfet,ncap 300 angles "Bent gate (PL.7)"')
    text = text.replace("scalefactor 50", "scalefactor 100 2")
    catalog = parse_magic(text, "gf180", "test.tech")
    assert catalog["styles"]["drc"]["scalefactor"] == [100, 2]
    assert catalog["single_layer"]["nfet,ncap"][0]["options"] == ["angles"]
    path = tmp_path / "rules.json"
    write_catalog(catalog, path)
    first = path.read_bytes()
    assert json.loads(first) == catalog
    write_catalog(catalog, path)
    assert path.read_bytes() == first


@pytest.mark.parametrize("rule", [
    'width a bad "Message"',
    'width a -1 "Message"',
    'width a 1e999 "Message"',
    'spacing a b 2 unknown "Message"',
    'surround a b 2 1 sideways "Message"',
    'area a 2 "Missing minedge"',
    'width a 100 Missing quoted message',
    'width a 100 "Unclosed',
])
def test_invalid_rules_fail_explicitly(rule: str) -> None:
    with pytest.raises(ValueError):
        parse_magic(deck(rule), "test", "test.tech")


@pytest.mark.parametrize("text", [
    'width a 1 "No drc"',
    'drc\nscalefactor 10\nwidth a 1 "No end"',
    'drc\nwidth a 1 "No scale"\nend',
    'drc\nscalefactor 0\nwidth a 1 "Zero scale"\nend',
    'drc\nscalefactor 10\nvariants invalid\nwidth a 1 "Bad variant"\nend',
    'drc\nscalefactor 10\ncifwidth a 1 "Unsupported only"\nend',
])
def test_invalid_sections_fail_explicitly(text: str) -> None:
    with pytest.raises(ValueError):
        parse_magic(text, "test", "test.tech")


def test_unterminated_continuation_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unterminated"):
        list(logical_lines("width a \\"))


def test_unsupported_command_with_invalid_message_is_counted() -> None:
    catalog = parse_magic(deck(
        'rect_only a "Unclosed unsupported message\n'
        'width a 100 "Supported width (a.1)"'
    ), "sky130", "test.tech")
    assert catalog["extracted_count"] == 1
    assert catalog["omitted_commands"] == {"rect_only": 1}
