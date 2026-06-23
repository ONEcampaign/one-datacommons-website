"""E2E engine tests: dev-finance goldens via offline_resolve.

Covers definite paths, no-data scenarios, and edge cases.
"""
from __future__ import annotations

from qre.models import (
    DefiniteResponse,
    NoDataResponse,
    RawTextInput,
    ResolveRequest,
)
from tests.engine._harness import offline_resolve


def make_request(query: str) -> ResolveRequest:
    return ResolveRequest(input=RawTextInput(query=query))


class TestDefiniteGoldens:
    def test_df01_health_oda_grants_usa_to_ethiopia(self):
        result = offline_resolve(make_request("health ODA grants from USA to Ethiopia"))
        inner = result.root
        assert inner.status == "definite"
        assert isinstance(inner, DefiniteResponse)
        spec = inner.interpretation
        # Check five-tuple
        assert spec.shape.population_type.dcid == "DevelopmentFinance"
        assert spec.shape.measured_property.dcid == "DevelopmentFinanceFlow"
        # Check SV
        sv_dcids = [sv.ref.dcid for sv in spec.stat_vars]
        assert "ONE/CRS_DAC/Health-ODAGrants-ETH" in sv_dcids
        # Check slots
        slot_map = {s.key.property.dcid: s for s in spec.slots if s.key.property}
        assert slot_map["DevelopmentFinanceScheme"].binding.kind == "value"
        assert slot_map["DevelopmentFinancePurpose"].binding.kind == "value"
        assert slot_map["DevelopmentFinanceRecipient"].binding.kind == "value"

    def test_df02_malaria_control_oda_grants_usa_to_ethiopia(self):
        result = offline_resolve(make_request("malaria control ODA grants from USA to Ethiopia"))
        inner = result.root
        assert inner.status == "definite"
        assert isinstance(inner, DefiniteResponse)
        spec = inner.interpretation
        # Check five-tuple
        assert spec.shape.population_type.dcid == "DevelopmentFinance"
        assert spec.shape.measured_property.dcid == "DevelopmentFinanceFlow"
        # Check SV
        sv_dcids = [sv.ref.dcid for sv in spec.stat_vars]
        assert "ONE/CRS_DAC/Malariacontrol-ODAGrants-ETH" in sv_dcids
        # Check slots
        slot_map = {s.key.property.dcid: s for s in spec.slots if s.key.property}
        assert slot_map["DevelopmentFinanceScheme"].binding.kind == "value"
        assert slot_map["DevelopmentFinancePurpose"].binding.kind == "value"
        assert slot_map["DevelopmentFinancePurpose"].binding.value.ref.dcid == "DAC/Malariacontrol"
        assert slot_map["DevelopmentFinanceRecipient"].binding.kind == "value"
        assert slot_map["DevelopmentFinanceRecipient"].binding.value.ref.dcid == "country/ETH"

    def test_df03_health_oda_grants_uk_to_kenya(self):
        result = offline_resolve(make_request("health ODA grants from UK to Kenya"))
        inner = result.root
        assert inner.status == "definite"
        spec = inner.interpretation
        sv_dcids = [sv.ref.dcid for sv in spec.stat_vars]
        assert "ONE/CRS_DAC/Health-ODAGrants-KEN" in sv_dcids
        slot_map = {s.key.property.dcid: s for s in spec.slots if s.key.property}
        assert slot_map["DevelopmentFinanceRecipient"].binding.value.ref.dcid == "country/KEN"

    def test_df04_official_development_assistance_germany_to_ethiopia(self):
        result = offline_resolve(
            make_request("official development assistance from Germany to Ethiopia")
        )
        inner = result.root
        assert inner.status == "definite"
        spec = inner.interpretation
        sv_dcids = [sv.ref.dcid for sv in spec.stat_vars]
        assert "ONE/CRS_DAC/Health-OfficialDevelopmentAssistance-ETH" in sv_dcids

    def test_df05_hiv_aids_oda_grants_usa_to_kenya(self):
        result = offline_resolve(make_request("HIV/AIDS ODA grants from USA to Kenya"))
        inner = result.root
        assert inner.status == "definite"
        spec = inner.interpretation
        sv_dcids = [sv.ref.dcid for sv in spec.stat_vars]
        assert "ONE/CRS_DAC/STDcontrolincludingHIVAIDS-ODAGrants-KEN" in sv_dcids

    def test_df06_health_oda_grants_to_ethiopia_no_donor(self):
        result = offline_resolve(make_request("health ODA grants to Ethiopia"))
        inner = result.root
        assert inner.status == "definite"
        spec = inner.interpretation
        sv_dcids = [sv.ref.dcid for sv in spec.stat_vars]
        assert "ONE/CRS_DAC/Health-ODAGrants-ETH" in sv_dcids
        # ETH should be directional (seam=ON default)
        entity_roles = {e.ref.dcid: e.role for e in spec.entities}
        assert entity_roles["country/ETH"].kind == "directional"

    def test_df07_basic_health_oda_grants_to_kenya(self):
        result = offline_resolve(make_request("basic health ODA grants to Kenya"))
        inner = result.root
        assert inner.status == "definite"
        spec = inner.interpretation
        sv_dcids = [sv.ref.dcid for sv in spec.stat_vars]
        assert "ONE/CRS_DAC/BasicHealth-ODAGrants-KEN" in sv_dcids

    def test_df08_reproductive_health_oda_grants_to_ethiopia(self):
        result = offline_resolve(make_request("reproductive health ODA grants to Ethiopia"))
        inner = result.root
        assert inner.status == "definite"
        spec = inner.interpretation
        sv_dcids = [sv.ref.dcid for sv in spec.stat_vars]
        assert "ONE/CRS_DAC/Reproductivehealthcare-ODAGrants-ETH" in sv_dcids

    def test_df09_health_aid_to_kenya_unbound_scheme(self):
        result = offline_resolve(make_request("health aid to Kenya"))
        inner = result.root
        assert inner.status == "definite"
        spec = inner.interpretation
        # Scheme should be unbound
        slot_map = {s.key.property.dcid: s for s in spec.slots if s.key.property}
        assert slot_map["DevelopmentFinanceScheme"].binding.kind == "unbound"
        # No specific SV dcids
        assert spec.stat_vars == []
        # But has coverage with has_data=True
        assert spec.coverage.has_data is True

    def test_df10_education_oda_to_india_set(self):
        result = offline_resolve(make_request("education ODA to India"))
        inner = result.root
        assert inner.status == "definite"
        spec = inner.interpretation
        sv_dcids = [sv.ref.dcid for sv in spec.stat_vars]
        assert "ONE/CRS_DAC/Healtheducation-OfficialDevelopmentAssistance-IND" in sv_dcids
        assert "ONE/CRS_DAC/Medicaleducationtraining-OfficialDevelopmentAssistance-IND" in sv_dcids
        # Purpose should be set
        slot_map = {s.key.property.dcid: s for s in spec.slots if s.key.property}
        assert slot_map["DevelopmentFinancePurpose"].binding.kind == "set"

    def test_df13_basic_health_oda_grants_france_to_kenya(self):
        result = offline_resolve(make_request("basic health ODA grants from France to Kenya"))
        inner = result.root
        assert inner.status == "definite"
        spec = inner.interpretation
        sv_dcids = [sv.ref.dcid for sv in spec.stat_vars]
        assert "ONE/CRS_DAC/BasicHealth-ODAGrants-KEN" in sv_dcids
        # FRA is subject (donor), KEN is directional (recipient)
        entity_roles = {e.ref.dcid: e.role for e in spec.entities}
        assert entity_roles["country/FRA"].kind == "subject"
        assert entity_roles["country/KEN"].kind == "directional"


class TestNoDataGoldens:
    def test_df11_per_capita_denominator_not_available(self):
        result = offline_resolve(make_request("health ODA per capita to Ethiopia"))
        inner = result.root
        assert inner.status == "no_data"
        assert isinstance(inner, NoDataResponse)
        assert inner.no_data.reason == "denominator_not_available"

    def test_df12_health_oda_nauru_no_observations(self):
        result = offline_resolve(make_request("health ODA grants from USA to Nauru"))
        inner = result.root
        assert inner.status == "no_data"
        assert isinstance(inner, NoDataResponse)
        assert inner.no_data.reason == "no_observations"

    def test_nd02_atlantis_entity_not_resolved(self):
        result = offline_resolve(
            make_request("official development assistance to the Republic of Atlantis")
        )
        inner = result.root
        assert inner.status == "no_data"
        assert isinstance(inner, NoDataResponse)
        assert inner.no_data.reason == "entity_not_resolved"


class TestExtraEdgeCases:
    def test_whitespace_query_no_llm_graph_call(self):
        result = offline_resolve(make_request("   "))
        inner = result.root
        assert inner.status == "no_data"
        assert isinstance(inner, NoDataResponse)
        assert inner.no_data.reason == "variable_not_resolved"

    def test_empty_query(self):
        result = offline_resolve(make_request(""))
        inner = result.root
        assert inner.status == "no_data"
        assert inner.no_data.reason == "variable_not_resolved"

    def test_query_echo_populated(self):
        result = offline_resolve(make_request("health ODA grants from USA to Ethiopia"))
        echo = result.root.query_echo
        assert echo.raw_query == "health ODA grants from USA to Ethiopia"
        assert echo.entry_path == "raw_text"
        assert not echo.extract_skipped
