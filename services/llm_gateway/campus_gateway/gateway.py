"""The gateway binds an authorized route to exactly one deployment and journals every call."""
from __future__ import annotations

from campus_contracts.models import Deployment, RoutingDecision
from campus_domain.errors import CampusError, ErrorCode

from .port import Adapter, InvocationJournal, LLMRequest, LLMResponse


class Gateway:
    def __init__(self, adapters: dict[str, Adapter], journal: InvocationJournal):
        self._adapters = adapters
        self._journal = journal

    def invoke(self, request: LLMRequest, deployment: Deployment, decision: RoutingDecision) -> LLMResponse:
        if decision.state != "authorized":
            raise CampusError(
                ErrorCode.ROUTE_NOT_AUTHORIZED,
                f"Routingentscheid {decision.id} ist {decision.state}, nicht autorisiert",
                blocked_action="llm.invoke",
            )
        if deployment.id not in {decision.selected_deployment_id, decision.review_deployment_id}:
            raise CampusError(
                ErrorCode.ROUTE_NOT_AUTHORIZED,
                "Deployment ist nicht Teil der autorisierten Route; keine stille Ersetzung (I08)",
                blocked_action="llm.invoke",
            )
        if request.deployment_id != deployment.id:
            raise CampusError(ErrorCode.ROUTE_NOT_AUTHORIZED, "Anfrage und Deployment stimmen nicht überein")
        adapter = self._adapters.get(deployment.adapter)
        if adapter is None:
            raise CampusError(
                ErrorCode.ADAPTER_NOT_APPROVED,
                f"Adapter '{deployment.adapter}' ist in dieser Ausbaustufe nicht freigegeben; es wird nichts simuliert",
                blocked_action="llm.invoke",
                required_resolution="Adapter als eigene Softwareänderung freigeben (A01)",
            )
        self._journal.begin(request, deployment, decision.id)
        try:
            response = adapter.invoke(request, deployment)
        except TimeoutError as exc:
            # The provider may have processed the request: reconcile before any retry (chapter 16).
            self._journal.mark_unknown(request.invocation_id, str(exc))
            raise CampusError(
                ErrorCode.OUTCOME_UNKNOWN,
                "Antwort ausgeblieben; Ausgang unbekannt, Reserve bleibt gebunden",
                blocked_action="llm.invoke",
                retryable=False,
                required_resolution="Aufrufjournal abgleichen, dann begründet erneut ausführen",
            ) from exc
        except Exception as exc:
            self._journal.fail(request.invocation_id, f"{type(exc).__name__}: {exc}")
            raise CampusError(
                ErrorCode.OUTCOME_UNKNOWN if not isinstance(exc, ConnectionError) else ErrorCode.NO_ELIGIBLE_MODEL,
                f"Providerfehler: {type(exc).__name__}",
                blocked_action="llm.invoke",
                retryable=isinstance(exc, ConnectionError),
                required_resolution="Route unter denselben Regeln neu bestimmen (kein stiller Fallback)",
            ) from exc
        self._journal.complete(response)
        return response
