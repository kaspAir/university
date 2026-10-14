"""Internet service and library (architecture chapters 12, 14): controlled fetch, versioned sources, locatable passages,
retraction with impact analysis.

The fetch pipeline: export check -> URL normalization -> DNS resolution and address check -> controlled GET without
automatic redirects (every hop re-checked) -> size and type limits -> isolated extraction -> versioned source with passages.
"""
from __future__ import annotations

import hashlib
import re
import socket
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from html.parser import HTMLParser

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from campus_domain import sources as rules
from campus_domain.authz import authorize
from campus_domain.errors import CampusError, ErrorCode
from campus_infra.audit import Actor, record
from campus_infra.ids import new_id, utcnow
from campus_infra.models import (
    ClaimVersion,
    DependencyEdge,
    EvidenceLink,
    Project,
    ReassessmentTask,
    Source,
    SourcePassage,
    SourceVersion,
    Task,
)

EXTRACTOR_VERSION = "campus-extract/1"
SUPPORTED_TYPES = ("text/html", "text/plain", "application/xhtml+xml", "application/json")
TIMEOUT_SECONDS = 60.0  # per hop; a slower server is a recorded gap, never a failed run
MIN_PASSAGE_CHARS = 40
REASSESSMENT_DUE_DAYS = 14

Resolver = Callable[[str], list[str]]


def system_resolver(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return []
    return sorted({info[4][0] for info in infos})


class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "noscript", "template", "svg"}
    _BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article", "blockquote", "pre", "td", "th"}

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BLOCK:
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self._BLOCK:
            self._parts.append("\n")

    def handle_data(self, data):
        if not self._skip_depth:
            self._parts.append(data)

    def text(self) -> str:
        raw = "".join(self._parts)
        raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
        raw = re.sub(r"\n\s*\n+", "\n\n", raw)
        return raw.strip()


def extract_text(content_type: str, body: bytes) -> str:
    base = content_type.split(";")[0].strip().lower()
    if base not in SUPPORTED_TYPES:
        raise CampusError(ErrorCode.UNSUPPORTED_CONTENT, f"Inhaltstyp {base or 'unbekannt'} wird in S2 nicht extrahiert (Zugangslücke)",
                          blocked_action="source.fetch", required_resolution="Konverter für diesen Typ als eigene Komponente freigeben")
    text = body.decode("utf-8", errors="replace")
    if base in {"text/html", "application/xhtml+xml"}:
        parser = _TextExtractor()
        parser.feed(text)
        return parser.text()
    return text.strip()


def split_passages(text: str) -> list[tuple[int, int]]:
    """Paragraph passages as (start, end) offsets into `text`. Offsets are the locator; text[start:end] reproduces the passage."""
    spans: list[tuple[int, int]] = []
    for match in re.finditer(r"[^\n]+(?:\n(?!\n)[^\n]+)*", text):
        start, end = match.start(), match.end()
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if end - start >= MIN_PASSAGE_CHARS:
            spans.append((start, end))
    if not spans and text.strip():
        spans.append((0, len(text)))
    return spans


@dataclass(frozen=True)
class FetchResult:
    final_url: str
    status: int
    content_type: str
    body: bytes
    redirect_chain: list[str]


class Fetcher:
    """Controlled egress. `resolver` and `transport` are injectable so tests never touch the network."""

    def __init__(self, resolver: Resolver = system_resolver, transport: httpx.BaseTransport | None = None):
        self._resolver = resolver
        self._transport = transport

    def fetch(self, url: str, task_data_labels: list[str]) -> FetchResult:
        target = rules.normalize_url(url)
        rules.export_check(target, task_data_labels)
        chain: list[str] = []
        current = target
        with httpx.Client(transport=self._transport, follow_redirects=False, timeout=TIMEOUT_SECONDS,
                          headers={"User-Agent": "phronesis-campus-fetch/0.1"}) as client:
            for _hop in range(rules.MAX_REDIRECTS + 1):
                rules.check_target(current, self._resolver(current.host))
                chain.append(current.url)
                try:
                    with client.stream("GET", current.url) as response:
                        if response.is_redirect:
                            location = response.headers.get("location")
                            if not location:
                                raise CampusError(ErrorCode.FETCH_FAILED, "Weiterleitung ohne Ziel", blocked_action="source.fetch")
                            current = rules.normalize_url(str(response.url.join(location)))
                            rules.export_check(current, task_data_labels)
                            continue  # the next hop is checked like the first one (T07)
                        if response.status_code >= 400:
                            raise CampusError(ErrorCode.FETCH_FAILED, f"HTTP {response.status_code} von {current.host}",
                                              blocked_action="source.fetch", retryable=response.status_code >= 500,
                                              required_resolution="Zugangslücke dokumentieren oder legitimen alternativen Zugang suchen")
                        content_type = response.headers.get("content-type", "")
                        body = bytearray()
                        for chunk in response.iter_bytes():
                            body.extend(chunk)
                            if len(body) > rules.MAX_BYTES:
                                raise CampusError(ErrorCode.FETCH_FAILED, f"Antwort grösser als {rules.MAX_BYTES} Bytes", blocked_action="source.fetch")
                        return FetchResult(final_url=current.url, status=response.status_code, content_type=content_type,
                                           body=bytes(body), redirect_chain=chain)
                except httpx.TimeoutException as exc:
                    raise CampusError(ErrorCode.FETCH_FAILED, f"Zeitüberschreitung bei {current.host}", blocked_action="source.fetch",
                                      retryable=True) from exc
                except httpx.HTTPError as exc:
                    raise CampusError(ErrorCode.FETCH_FAILED, f"Abruf fehlgeschlagen: {type(exc).__name__}", blocked_action="source.fetch",
                                      retryable=True) from exc
        raise CampusError(ErrorCode.FETCH_FAILED, "Zu viele Weiterleitungen", blocked_action="source.fetch")


default_fetcher: Fetcher = Fetcher()


def _own(db: Session, model, object_id: str, actor: Actor, what: str):
    row = db.get(model, object_id)
    if row is None or row.workspace_id != actor.workspace_id:
        raise CampusError(ErrorCode.NOT_FOUND, f"{what} nicht gefunden")
    return row


def fetch_source(db: Session, *, actor: Actor, project_id: str, url: str, task_id: str | None, correlation_id: str,
                 fetcher: Fetcher | None = None) -> tuple[Source, SourceVersion, list[SourcePassage], bool]:
    """Register a new permitted public source without per-URL human approval (T15). Returns (source, version, passages, new_version)."""
    authorize("source.fetch", actor.roles)
    project = _own(db, Project, project_id, actor, "Projekt")
    labels = ["public"]
    if task_id:
        task = _own(db, Task, task_id, actor, "Aufgabe")
        labels = list(task.task_need["data_labels"])
    fetcher = fetcher or default_fetcher
    try:
        result = fetcher.fetch(url, labels)
    except CampusError as err:
        record(db, actor=actor, command="source.fetch", object_type="source", object_id=url[:80], object_version=None,  # column holds 80
               correlation_id=correlation_id, policy_decision=f"blocked:{err.code.value}", project_id=project.id,
               payload={"message": err.message})
        db.commit()
        err.details["audited"] = True
        raise
    text = extract_text(result.content_type, result.body)
    target = rules.normalize_url(url)
    source = db.execute(select(Source).where(Source.project_id == project.id, Source.normalized_url == target.url)).scalar_one_or_none()
    if source is None:
        source = Source(id=new_id("Q"), workspace_id=actor.workspace_id, project_id=project.id, normalized_url=target.url, host=target.host)
        db.add(source)
        db.flush()
    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    latest = db.execute(select(SourceVersion).where(SourceVersion.source_id == source.id).order_by(SourceVersion.version.desc())).scalars().first()
    if latest is not None and latest.text_sha256 == text_hash:
        passages = db.execute(select(SourcePassage).where(SourcePassage.source_version_id == latest.id).order_by(SourcePassage.ordinal)).scalars().all()
        return source, latest, list(passages), False
    version = SourceVersion(
        id=new_id("QV"), workspace_id=actor.workspace_id, source_id=source.id, version=(latest.version + 1) if latest else 1,
        final_url=result.final_url, http_status=result.status, content_type=result.content_type.split(";")[0].strip().lower(),
        raw_sha256=hashlib.sha256(result.body).hexdigest(), raw_bytes=len(result.body), text_sha256=text_hash, extracted_text=text,
        extractor_version=EXTRACTOR_VERSION, redirect_chain=result.redirect_chain, fetched_by_task_id=task_id,
    )
    db.add(version)
    db.flush()
    passages = []
    for ordinal, (start, end) in enumerate(split_passages(text), start=1):
        passage_text = text[start:end]
        passages.append(SourcePassage(id=new_id("SP"), workspace_id=actor.workspace_id, source_version_id=version.id, ordinal=ordinal,
                                      start_offset=start, end_offset=end, text=passage_text,
                                      text_sha256=hashlib.sha256(passage_text.encode("utf-8")).hexdigest()))
    db.add_all(passages)
    db.flush()
    record(db, actor=actor, command="source.fetch", object_type="source", object_id=source.id, object_version=str(version.version),
           correlation_id=correlation_id, project_id=project.id, event_type="SourceFetched",
           payload={"host": source.host, "passages": len(passages), "raw_sha256": version.raw_sha256, "redirects": len(result.redirect_chain) - 1,
                    "task_id": task_id})
    return source, version, passages, True


def retract_source(db: Session, *, actor: Actor, source_id: str, reason: str, due_days: int | None, correlation_id: str) -> ReassessmentTask | None:
    """Art. 23 / T09: a retracted source marks dependent claims for reassessment and creates a dated task. Nothing is deleted
    and no claim is declared false by the software."""
    authorize("source.retract", actor.roles)
    source = _own(db, Source, source_id, actor, "Quelle")
    if not reason.strip():
        raise CampusError(ErrorCode.VALIDATION_ERROR, "Rückzug braucht einen Grund")
    if source.status == "retracted":
        raise CampusError(ErrorCode.INVALID_TRANSITION, "Quelle ist bereits zurückgezogen")
    source.status = "retracted"
    source.retraction_reason = reason
    affected = db.execute(
        select(ClaimVersion).join(EvidenceLink, EvidenceLink.claim_version_id == ClaimVersion.id)
        .join(SourcePassage, SourcePassage.id == EvidenceLink.source_passage_id)
        .join(SourceVersion, SourceVersion.id == SourcePassage.source_version_id)
        .where(SourceVersion.source_id == source.id)
    ).scalars().unique().all()
    affected = list(affected)
    seen = {c.id for c in affected}
    frontier = list(seen)
    while frontier:  # dependents of dependents are affected too (chapter 14: dependency edges stay and propagate)
        edges = db.execute(select(DependencyEdge).where(DependencyEdge.from_claim_version_id.in_(frontier))).scalars().all()
        frontier = []
        for edge in edges:
            if edge.to_claim_version_id not in seen:
                seen.add(edge.to_claim_version_id)
                frontier.append(edge.to_claim_version_id)
                affected.append(db.get(ClaimVersion, edge.to_claim_version_id))
    for claim in affected:
        claim.reassessment_required = True
    task: ReassessmentTask | None = None
    if affected:
        task = ReassessmentTask(id=new_id("RA"), workspace_id=actor.workspace_id, project_id=source.project_id, trigger="SourceRetracted",
                                trigger_object_id=source.id, affected_claim_version_ids=[c.id for c in affected],
                                responsible_role="research_director", due_at=utcnow() + timedelta(days=due_days or REASSESSMENT_DUE_DAYS))
        db.add(task)
    db.flush()
    record(db, actor=actor, command="source.retract", object_type="source", object_id=source.id, object_version=None,
           correlation_id=correlation_id, project_id=source.project_id, event_type="SourceRetracted",
           payload={"reason": reason, "affected_claim_version_ids": [c.id for c in affected], "reassessment_task_id": task.id if task else None})
    return task


def project_sources(db: Session, *, actor: Actor, project_id: str) -> list[dict]:
    _own(db, Project, project_id, actor, "Projekt")
    out = []
    for source in db.execute(select(Source).where(Source.project_id == project_id).order_by(Source.created_at)).scalars():
        versions = db.execute(select(SourceVersion).where(SourceVersion.source_id == source.id).order_by(SourceVersion.version)).scalars().all()
        out.append({"source_id": source.id, "url": source.normalized_url, "host": source.host, "status": source.status,
                    "retraction_reason": source.retraction_reason,
                    "versions": [{"source_version_id": v.id, "version": v.version, "fetched_at": v.fetched_at, "final_url": v.final_url,
                                  "content_type": v.content_type, "raw_sha256": v.raw_sha256, "text_sha256": v.text_sha256,
                                  "extractor_version": v.extractor_version, "redirect_chain": v.redirect_chain,
                                  "passages": [dict(m) for m in db.execute(
                                      select(SourcePassage.id, SourcePassage.ordinal, SourcePassage.start_offset, SourcePassage.end_offset)
                                      .where(SourcePassage.source_version_id == v.id).order_by(SourcePassage.ordinal)).mappings().all()]}
                                 for v in versions]})
    return out
