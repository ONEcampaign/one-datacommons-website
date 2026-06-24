"""Tests for families.registry.rule_for.

Invariant: CRS_DAC dcids always match the dev-finance rule first and must never
reach the standard catch-all, even when standard SVs are mixed in the same list.
"""
from __future__ import annotations

from qre.engine.families.dev_finance import DEV_FINANCE_RULE
from qre.engine.families.registry import STANDARD_RULE, rule_for


class TestRuleForDevFinance:
    """CRS_DAC candidates always route to the dev-finance rule (before catch-all)."""

    def test_single_crs_dac_sv_returns_dev_finance_rule(self):
        rule = rule_for(candidate_svs=["ONE/CRS_DAC/Health-ODAGrants-ETH"])
        assert rule is DEV_FINANCE_RULE

    def test_crs_dac_prefix_variant_matches(self):
        rule = rule_for(candidate_svs=["ONE/CRS_DAC/Water-ODAGrants-KEN"])
        assert rule is DEV_FINANCE_RULE

    def test_mixed_list_with_one_crs_dac_still_matches(self):
        # detect is recall-only and may mix namespaces; one CRS_DAC dcid is enough
        rule = rule_for(candidate_svs=["Count_Person", "ONE/CRS_DAC/Health-ODAGrants-ETH"])
        assert rule is DEV_FINANCE_RULE

    def test_dev_finance_rule_label(self):
        label_lower = DEV_FINANCE_RULE.label.lower()
        assert "crs dac" in label_lower or "development finance" in label_lower

    def test_dev_finance_rule_namespace(self):
        assert DEV_FINANCE_RULE.namespace == "ONE/CRS_DAC/"

    def test_crs_dac_never_reaches_standard_catch_all(self):
        """Core registry ordering invariant: dev-finance must claim CRS_DAC dcids first."""
        crs_dcids = [
            "ONE/CRS_DAC/Health-ODAGrants-ETH",
            "ONE/CRS_DAC/Water-ODALoans-KEN",
            "ONE/CRS_DAC/Agriculture-OfficialDevelopmentAssistance-IND",
        ]
        for dcid in crs_dcids:
            rule = rule_for(candidate_svs=[dcid])
            assert rule is not STANDARD_RULE, (
                f"CRS_DAC dcid {dcid!r} reached the standard catch-all — "
                "dev-finance rule must be registered before the standard rule"
            )
            assert rule is DEV_FINANCE_RULE


class TestRuleForStandard:
    """Standard DC SVs route to the standard catch-all rule."""

    def test_count_person_returns_standard_rule(self):
        rule = rule_for(candidate_svs=["Count_Person"])
        assert rule is STANDARD_RULE

    def test_gdp_sv_returns_standard_rule(self):
        rule = rule_for(
            candidate_svs=["Amount_EconomicActivity_GrossDomesticProduction_Nominal"]
        )
        assert rule is STANDARD_RULE

    def test_multiple_standard_svs_return_standard_rule(self):
        standard_svs = [
            "Count_Person",
            "Median_Income_Person",
            "FertilityRate_Person_Female",
            "MortalityRate_Person",
        ]
        rule = rule_for(candidate_svs=standard_svs)
        assert rule is STANDARD_RULE

    def test_standard_rule_label(self):
        assert "standard" in STANDARD_RULE.label.lower()

    def test_standard_rule_is_last_resort(self):
        """Standard rule has empty namespace (catch-all, no prefix restriction)."""
        assert STANDARD_RULE.namespace == ""


class TestRuleForEmpty:
    """Empty candidate list returns None (no detection result)."""

    def test_empty_list_returns_none(self):
        rule = rule_for(candidate_svs=[])
        assert rule is None
