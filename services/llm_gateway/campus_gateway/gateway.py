"""The gateway binds an authorized route to exactly one deployment and journals every call.

Failure mapping follows the policy's fallback block: technical failures re-enter route selection
(no adapter-level retry, no hidden model substitution), refusals are never routed around, an unknown
outcome stays unknown until reconciled.
"""
from __future__ import annotations

from campus_contracts.models import Deployment, RoutingDecision
from campus_domain.errors import CampusError, ErrorCode

from .port import (
    Adapter,
    CapabilityUnsupported,
    InvocationJournal,
    LLMRequest,
    LLMResponse,
    ProviderRefusal,
    ProviderRejected,
    ProviderTimeout,
    ProviderUnavailable,
    SecretUnavailable,
)


class Gateway:
    def __init__(self, adapters: dict[str, Adapter], journal: InvocationJournal):
        self._adapters = adapters

        self._journal = journal

    @property
    def approved_adapters(self) -> list[str]:
        return sorted(self._adapters)

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
                required_resolution="Adapter als eigene Softwareänderung freigeben (A01, CAMPUS_APPROVED_ADAPTERS)",
            )
        if request.output_contract is not None and deployment.capabilities.structured_output != "supported":
            raise CampusError(
                ErrorCode.CAPABILITY_UNSUPPORTED,
                "Strukturierte Ausgabe ist für dieses Deployment nicht als unterstützt eingetragen; kein JSON-Textersatz (A08)",
                blocked_action="llm.invoke",
            )
        self._journal.begin(request, deployment, decision.id)
        try:
            response = adapter.invoke(request, deployment)
        except ProviderTimeout as exc:
            # The provider may have processed the request: reconcile before any retry (chapter 16).
            self._journal.mark_unknown(request.invocation_id, str(exc))
            raise CampusError(
                ErrorCode.OUTCOME_UNKNOWN,
                "Antwort ausgeblieben; Ausgang unbekannt, Reserve bleibt gebunden",
                blocked_action="llm.invoke",
                retryable=False,
                required_resolution="Aufrufjournal abgleichen, dann begründet erneut ausführen",
            ) from exc
        except ProviderUnavailable as exc:
            self._journal.fail(request.invocation_id, f"unavailable: {exc}")
            raise CampusError(
                ErrorCode.NO_ELIGIBLE_MODEL,
                f"Provider nicht verfügbar: {exc}",
                blocked_action="llm.invoke",
                retryable=True,
                required_resolution="Route unter denselben Regeln neu bestimmen (replan_under_same_constraints)",
            ) from exc
        except ProviderRefusal as exc:
            self._journal.fail(request.invocation_id, f"refusal: {exc.category}")
            raise CampusError(
                ErrorCode.PROTECTED_REFUSAL,
                f"Provider hat aus Schutzgründen abgelehnt (Kategorie {exc.category or 'unbekannt'}); kein Anbieterwechsel",
                blocked_action="llm.invoke",
                retryable=False,
                required_resolution="Aufgabe fachlich prüfen; Schutzverweigerung wird nicht umgangen (Kap. 10)",
            ) from exc
        except ProviderRejected as exc:
            self._journal.fail(request.invocation_id, f"rejected: {exc}")
            raise CampusError(
                ErrorCode.PROVIDER_REJECTED,
                f"Provider hat die Anfrage zurückgewiesen: {exc}",
                blocked_action="llm.invoke",
                retryable=False,
                required_resolution="Deployment-Konfiguration (Modellkennung, Zugang, Vertrag) prüfen",
            ) from exc
        except CapabilityUnsupported as exc:
            self._journal.fail(request.invocation_id, f"capability: {exc}")
            raise CampusError(
                ErrorCode.CAPABILITY_UNSUPPORTED, str(exc), blocked_action="llm.invoke", retryable=False,
                required_resolution="Fähigkeit im Katalog korrigieren oder anderes Deployment qualifizieren",
            ) from exc
        except SecretUnavailable as exc:
            self._journal.fail(request.invocation_id, f"secret: {exc}")
            raise CampusError(
                ErrorCode.SECRET_UNAVAILABLE, str(exc), blocked_action="llm.invoke", retryable=False,
                required_resolution="Geheimnis im Gateway-Betrieb hinterlegen (nie im Katalogexport)",
            ) from exc
        except Exception as exc:
            # Anything else after send is ambiguous: record it and keep the reserve bound.
            self._journal.mark_unknown(request.invocation_id, f"{type(exc).__name__}: {exc}")
            raise CampusError(
                ErrorCode.OUTCOME_UNKNOWN,
                f"Unerwarteter Adapterfehler: {type(exc).__name__}",
                blocked_action="llm.invoke",
                retryable=False,
                required_resolution="Aufrufjournal abgleichen",
            ) from exc
        self._journal.complete(response)
        return response
