"""The limits /health and the UI show are derived from the running configuration, never from a claim."""
from __future__ import annotations

from campus_api.routers.health import pilot_limitations


def test_mock_only_names_the_mock_adapter_and_synthetic_results():
    lim = pilot_limitations("development_mock", "none", "dev-server-sqlite", ["mock"])
    assert any("Mock-Adapter, Ergebnisse synthetisch" in x for x in lim)
    assert any("Temporal im Entwicklungsmodus" in x for x in lim) and any("Keine Sandbox" in x for x in lim)
    assert not any("echten Adaptern" in x for x in lim)


def test_real_adapters_are_named_and_the_level_stays_limited():
    lim = pilot_limitations("development_mock", "none", "dev-server-sqlite", ["anthropic", "openai"])
    assert any("echten Adaptern (anthropic, openai)" in x and "kosten Geld" in x and "Egress-Proxy fehlt" in x for x in lim)
    assert not any("Mock-Adapter bleibt freigegeben" in x for x in lim), "no mock approved: nothing to warn about"


def test_mixed_approval_warns_that_mock_results_stay_synthetic():
    lim = pilot_limitations("development_mock", "none", "dev-server-sqlite", ["mock", "anthropic"])
    assert any("Mock-Adapter bleibt freigegeben" in x for x in lim) and any("echten Adaptern (anthropic)" in x for x in lim)
