"""An answer must never fail because of an output bound the campus invented: the bound handed to the provider is the deployment's
documented capacity, limited only by what the task's budget can still pay at the bound price (first live route, 13.10.2026)."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from campus_api import services_research
from campus_contracts.models import ModelCatalog
from campus_domain.budget import Pricing, affordable_output_tokens
from campus_infra.models import Reservation, Task
from tests.integration.test_s0_flow import Api, _authorize, _create_task, bootstrap, govern

pytestmark = pytest.mark.integration


def test_affordable_output_tokens_from_price_and_budget():
    price = Pricing("p", "CHF", input_per_million_minor=1000, output_per_million_minor=5000)  # CHF 10 / 50 per million
    assert affordable_output_tokens(price, budget_minor=64, input_tokens=12000) == 10400  # 64 - 12 = 52 Rappen -> 10 400 output tokens
    assert affordable_output_tokens(price, budget_minor=10, input_tokens=12000) == 0, "input alone exhausts the budget"
    assert affordable_output_tokens(Pricing("z", "CHF", 0, 0), 1, 1) is None, "free output has no monetary bound"


def test_bound_is_capacity_or_budget_never_a_constant(client, session_factory, workspace_id):
    boot = bootstrap(session_factory, workspace_id)
    api = Api(client)
    api.login(workspace_id, "leitung")
    state = govern(api, boot)
    # budget 0.70: generation reserves 32 and review 36 Rappen (12 000 + 4 000 tokens at 10/50 per million); 2 Rappen stay free
    task = _create_task(api, state["project"]["project_id"], budget="0.70", context_tokens_estimate=12000, max_output_tokens=4000)
    decision_id = _authorize(api, task["task_id"]).json()["routing_decision_id"]
    with session_factory() as db:
        row = db.get(Task, task["task_id"])
        catalog = ModelCatalog.model_validate(services_research.active_catalog(db, workspace_id).document)
        dep = catalog.deployment("fixture-deployment-a")  # documented output capacity 8 000
        pricing = services_research._pricing(db, workspace_id, dep.pricing_ref)
        generation = db.execute(select(Reservation).where(Reservation.routing_decision_id == decision_id, Reservation.purpose == "generation")).scalar_one()
        # free 2 + own reservation 32 = 34 Rappen; minus 12 for the input -> 22 Rappen -> 4 400 output tokens, below the capacity
        assert services_research._output_bound(db, row, dep, pricing, generation, 12000, 4000) == 4400
        # a generous budget leaves the documented capacity as the bound
        rich = generation.__class__(id="X", workspace_id=workspace_id, account_id=generation.account_id, task_id=row.id, purpose="generation",
                                    amount_minor=100_000, currency="CHF", state="reserved", routing_decision_id=decision_id)
        assert services_research._output_bound(db, row, dep, pricing, rich, 12000, 4000) == 8000
        # the need the route was admitted for is the floor even when the budget could not pay for it
        assert services_research._output_bound(db, row, dep, pricing, generation, 1_000_000, 4000) == 4000
