"""Tests for arb-scan query-param parsing (_parse_arb_params).

Guards the regression where a present-but-empty numeric field (e.g. the client
sending "sales_tax=") raised float("")/int("") and surfaced as an SSE error
instead of falling back to the default — the same fallback the LP scan applies.
"""
import importlib

lp_web = importlib.import_module("lp-web")


class TestParseArbParams:
    def test_defaults_when_absent(self):
        p = lp_web._parse_arb_params({})
        assert p["region"] == 10000002
        assert p["sales_tax"] == 0.075
        assert p["cross_station"] is True
        assert p["min_isk"] == 0
        assert p["max_jumps"] == 6
        assert p["avoid_lowsec"] is False
        assert p["route_flag"] == "shortest"

    def test_empty_strings_fall_back_to_defaults(self):
        # The whole point: empty values must not raise.
        p = lp_web._parse_arb_params({
            "region": [""], "sales_tax": [""], "min_isk": [""], "max_jumps": [""],
        })
        assert p["region"] == 10000002
        assert p["sales_tax"] == 0.075
        assert p["min_isk"] == 0
        assert p["max_jumps"] == 6

    def test_explicit_values_parsed(self):
        p = lp_web._parse_arb_params({
            "region": ["10000043"], "sales_tax": ["0.036"], "min_isk": ["1000000"],
            "max_jumps": ["10"], "cross_station": ["0"], "avoid_lowsec": ["1"],
            "route_flag": ["secure"],
        })
        assert p["region"] == 10000043
        assert p["sales_tax"] == 0.036
        assert p["min_isk"] == 1000000
        assert p["max_jumps"] == 10
        assert p["cross_station"] is False
        assert p["avoid_lowsec"] is True
        assert p["route_flag"] == "secure"

    def test_zero_sales_tax_is_respected_not_defaulted(self):
        # A genuine 0 tax must survive (unlike an empty string).
        p = lp_web._parse_arb_params({"sales_tax": ["0"]})
        assert p["sales_tax"] == 0.0
