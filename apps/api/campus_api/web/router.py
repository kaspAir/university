"""Pages under /campus. Reads go through the same services as the JSON API; the two actions (stop, mandate decision) go through the
JSON API from the browser with CSRF and Idempotency-Key, so there is exactly one write path."""
from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from campus_domain.errors import CampusError, ErrorCode
from campus_domain.money import Money
from campus_infra import budget_service
from campus_infra.audit import Actor, record
from campus_infra.models import (
    ArtifactVersion,
    AuditEvent,
    HumanDecision,
    Institute,
    MandateVersion,
    PilotStudy,
    Project,
    RoutingDecisionRecord,
    Source,
    Task,
    User,
    WorkflowRun,
    Workspace,
)

from .. import (
    services_dossier,
    services_experiments,
    services_factory,
    services_identity,
    services_pilot,
    services_research,
    services_sources,
)
from ..deps import current_actor, get_db
from ..errors import trace_id
from ..routers import governance, health
from ..security import SESSION_COOKIE, load_session, revoke_session
from ..settings import get_settings
from .steps import MANDATE_DEFAULTS, TASK_DEFAULTS, next_step

router = APIRouter(tags=["web"])
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

WEEKDAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")
STATUS_ORDER = ("observation", "derivation", "supported", "contested", "refuted", "hypothesis", "unresolved")
STATUS_LABELS = {"observation": "Beobachtung", "derivation": "Ableitung", "supported": "gestützt", "contested": "bestritten", "refuted": "widerlegt",
                 "hypothesis": "Hypothese", "unresolved": "ungeklärt"}
OPTION_LABELS = {"accept": "Angenommen", "accept_limited": "Begrenzt angenommen", "revise": "Zur Überarbeitung", "reject": "Abgelehnt"}


class LoginRequired(Exception):
    pass


def web_actor(request: Request, db: Session = Depends(get_db)) -> Actor:
    try:
        return current_actor(request, db)
    except CampusError as err:
        if err.code == ErrorCode.UNAUTHENTICATED:
            raise LoginRequired() from err
        raise


# ---------------------------------------------------------------- formatting

def fmt_money(minor: int | None, currency: str = "CHF") -> str:
    if minor is None:
        return "offen"
    return Money(int(minor), currency).format().replace(",", " ")


def fmt_dt(value) -> str:
    if value is None:
        return "–"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    return f"{WEEKDAYS[value.weekday()]} {value:%d.%m.%Y} · {value:%H:%M}"


def fmt_date(value) -> str:
    if value is None:
        return "–"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    return f"{WEEKDAYS[value.weekday()]} {value:%d.%m.%Y}"


templates.env.filters.update({"money": fmt_money, "dt": fmt_dt, "date": fmt_date, "time": lambda v: f"{v:%H:%M}" if v else "–",
                              "status_label": lambda s: STATUS_LABELS.get(s, s),
                              "option_label": lambda o: OPTION_LABELS.get(o, o)})


# ---------------------------------------------------------------- shared context

def _counters(db: Session, actor: Actor) -> dict:
    ws = actor.workspace_id
    projects = db.execute(select(func.count()).select_from(Project).where(Project.workspace_id == ws)).scalar() or 0
    inv = services_research.inventory(db, ws)
    open_decisions = db.execute(select(func.count()).select_from(MandateVersion).where(MandateVersion.workspace_id == ws, MandateVersion.state == "draft")).scalar() or 0
    sources = db.execute(select(func.count()).select_from(Source).join(Project, Project.id == Source.project_id).where(Project.workspace_id == ws)).scalar() or 0
    catalog = governance.catalog_status(actor=actor, db=db)
    models = sum(1 for d in catalog["deployments"] if d.get("enabled"))
    artifacts = db.execute(select(func.count()).select_from(ArtifactVersion).where(ArtifactVersion.workspace_id == ws)).scalar() or 0
    return {"dossiers": projects, "ergebnisse": artifacts, "register": len(inv["agents"]) + len(inv["skills"]), "modelle": models,
            "pruefung": open_decisions, "bibliothek": sources, "betrieb": None}


def _ctx(request: Request, db: Session, actor: Actor, view: str, **extra) -> dict:
    settings = get_settings()
    ws = db.get(Workspace, actor.workspace_id)
    user = db.get(User, actor.actor_id)
    return {"request": request, "view": view, "user": user, "roles": sorted(r.value for r in actor.roles), "workspace": ws,
            "capability_level": settings.capability_level, "mock": settings.capability_level == "development_mock",
            "now": fmt_dt(datetime.now(UTC)), "csrf": request.state.session_row.csrf_token, "counters": _counters(db, actor),
            "can_stop": bool({"research_director", "integrity_officer"} & {r.value for r in actor.roles}),
            "can_decide": "research_director" in {r.value for r in actor.roles}, "task_defaults_json": json.dumps(TASK_DEFAULTS), **extra}


def _render(name: str, ctx: dict) -> HTMLResponse:
    return templates.TemplateResponse(ctx["request"], name, ctx)


def _catalog_index(db: Session, actor: Actor) -> dict[str, dict]:
    return {d["id"]: d for d in governance.catalog_status(actor=actor, db=db)["deployments"]}


# ---------------------------------------------------------------- login / logout

@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db)):
    workspaces = db.execute(select(Workspace).order_by(Workspace.created_at)).scalars().all()
    return templates.TemplateResponse(request, "login.html", {"request": request, "error": None, "workspaces": workspaces,
                                                              "capability_level": get_settings().capability_level, "username": ""})


@router.post("/login", response_class=HTMLResponse)
def login_submit(request: Request, workspace_id: str = Form(...), username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    settings = get_settings()
    try:
        user, token, _csrf = services_identity.login(db, workspace_id=workspace_id.strip(), username=username.strip(), password=password,
                                                     ttl_hours=settings.session_ttl_hours, correlation_id=trace_id(request))
    except CampusError as err:
        db.rollback()
        workspaces = db.execute(select(Workspace).order_by(Workspace.created_at)).scalars().all()
        return templates.TemplateResponse(request, "login.html", {"request": request, "error": err.message, "workspaces": workspaces,
                                                                  "capability_level": settings.capability_level, "username": username}, status_code=401)
    db.commit()
    response = RedirectResponse("/campus", status_code=303)
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="strict", secure=settings.cookie_secure,
                        max_age=settings.session_ttl_hours * 3600, path="/")
    return response


@router.post("/logout")
def logout(request: Request, csrf_token: str = Form(...), db: Session = Depends(get_db)):
    """Form post: the CSRF token travels as a form field, so the session is checked here instead of through the header dependency."""
    row = load_session(db, request.cookies.get(SESSION_COOKIE))
    if row is None:
        raise LoginRequired()
    if csrf_token != row.csrf_token:
        raise CampusError(ErrorCode.CSRF_FAILED, "CSRF-Token stimmt nicht", blocked_action="auth.logout")
    user = db.get(User, row.user_id)
    actor = Actor(actor_type="user", actor_id=user.id, workspace_id=user.workspace_id)
    revoke_session(db, request.cookies.get(SESSION_COOKIE))
    record(db, actor=actor, command="auth.logout", object_type="user", object_id=actor.actor_id, object_version=None,
           correlation_id=trace_id(request), event_type="UserLoggedOut")
    db.commit()
    response = RedirectResponse("/campus/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


# ---------------------------------------------------------------- campus

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def campus(request: Request, actor: Actor = Depends(web_actor), db: Session = Depends(get_db)):
    ws = actor.workspace_id
    institutes = db.execute(select(Institute).where(Institute.workspace_id == ws).order_by(Institute.sort_order)).scalars().all()
    project_counts = dict(db.execute(select(Project.institute_id, func.count()).where(Project.workspace_id == ws).group_by(Project.institute_id)).all())
    task_states: dict[str, Counter] = {}
    for inst_id, state, count in db.execute(select(Project.institute_id, Task.state, func.count()).join(Task, Task.project_id == Project.id)
                                            .where(Project.workspace_id == ws).group_by(Project.institute_id, Task.state)).all():
        task_states.setdefault(inst_id, Counter())[state] = count
    tree = budget_service.tree(db, ws)
    by_id = {a["id"]: a for a in tree}
    inst_budget: dict[str, tuple[int, int]] = {}
    for p in db.execute(select(Project).where(Project.workspace_id == ws)).scalars().all():
        acc = by_id.get(p.budget_account_id)
        if acc:
            used, limit = inst_budget.get(p.institute_id, (0, 0))
            inst_budget[p.institute_id] = (used + acc["consumed_minor"], limit + acc["limit_minor"])
    inst_rows = []
    for i in institutes:
        states = task_states.get(i.id, Counter())
        used, limit = inst_budget.get(i.id, (0, 0))
        inst_rows.append({"id": i.id, "name": i.name, "remit": i.remit, "projects": project_counts.get(i.id, 0), "running": states.get("running", 0),
                          "waiting": states.get("waiting", 0) + states.get("planned", 0), "budget_pct": round(100 * used / limit) if limit else None})
    decisions = governance.open_decisions(state="open", actor=actor, db=db)
    mandates = {m["mandate_version_id"]: m for m in governance.list_mandates(actor=actor, db=db)}
    for d in decisions:
        m = mandates.get(d["object_id"])
        d["question_space"] = (m or {}).get("document", {}).get("question_space")
        d["knowledge_goal"] = (m or {}).get("document", {}).get("knowledge_goal")
    catalog = governance.catalog_status(actor=actor, db=db)
    events = db.execute(select(AuditEvent).where(AuditEvent.workspace_id == ws).order_by(AuditEvent.occurred_at.desc()).limit(12)).scalars().all()
    n = len(decisions)
    words = {0: "Keine Entscheide offen.", 1: "Ein Entscheid wartet.", 2: "Zwei Entscheide warten.", 3: "Drei Entscheide warten."}
    headline = "Guten Tag. " + words.get(n, f"{n} Entscheide warten.")
    return _render("campus.html", _ctx(request, db, actor, "campus", headline=headline, decisions=decisions, institutes=inst_rows, tree=tree,
                                       catalog=catalog, events=events))


# ---------------------------------------------------------------- dossiers

def _project_rows(db: Session, actor: Actor, institute_id: str | None) -> list[dict]:
    stmt = select(Project).where(Project.workspace_id == actor.workspace_id)
    if institute_id:
        stmt = stmt.where(Project.institute_id == institute_id)
    institutes = {i.id: i.name for i in db.execute(select(Institute).where(Institute.workspace_id == actor.workspace_id)).scalars().all()}
    mandates = {m["mandate_version_id"]: m for m in governance.list_mandates(actor=actor, db=db)}
    tree = {a["id"]: a for a in budget_service.tree(db, actor.workspace_id)}
    rows = []
    for p in db.execute(stmt.order_by(Project.created_at)).scalars().all():
        doc = services_dossier.dossier_document(db, actor=actor, project_id=p.id)
        counts = doc["status_axes"]["claims_by_epistemic_status"]
        acc = tree.get(p.budget_account_id, {})
        rows.append({"project_id": p.id, "title": p.title, "institute": institutes.get(p.institute_id, p.institute_id), "institute_id": p.institute_id,
                     "work_state": p.work_state, "mandate": mandates.get(p.mandate_version_id, {}).get("document", {}).get("title"),
                     "claims": counts, "claims_total": sum(counts.values()), "release": doc["status_axes"]["release"],
                     "open_findings": len(doc["status_axes"]["blocking"]["open_findings"]), "consumed": acc.get("consumed_minor"), "limit": acc.get("limit_minor"),
                     "currency": acc.get("currency", "CHF"), "synthetic": doc["declarations"]["synthetic_content"]})
    return rows


@router.get("/dossiers", response_class=HTMLResponse)
def dossiers(request: Request, institut: str | None = None, actor: Actor = Depends(web_actor), db: Session = Depends(get_db)):
    rows = _project_rows(db, actor, institut)
    inst_name = None
    if institut:
        inst = db.get(Institute, institut)
        inst_name = inst.name if inst else institut
    return _render("dossiers.html", _ctx(request, db, actor, "dossiers", rows=rows, institut=institut, inst_name=inst_name))


@router.get("/dossiers/{project_id}", response_class=HTMLResponse)
def dossier(project_id: str, request: Request, tab: str = "frage", actor: Actor = Depends(web_actor), db: Session = Depends(get_db)):
    doc = services_dossier.dossier_document(db, actor=actor, project_id=project_id)
    artifacts = services_dossier.project_artifacts(db, actor=actor, project_id=project_id)
    catalog = _catalog_index(db, actor)
    institute = db.get(Institute, doc["project"]["institute_id"])
    runs = []
    for r in doc["runs"]:
        dec = db.get(RoutingDecisionRecord, r["routing_decision_id"]) if r.get("routing_decision_id") else None
        d = dec.document if dec else {}
        sel = catalog.get(d.get("selected_deployment_id") or "", {})
        rev = catalog.get(d.get("review_deployment_id") or "", {}) if d.get("review_deployment_id") else {}
        excluded = [a for a in d.get("candidate_assessments", []) if not a.get("eligible")]
        runs.append({**r, "selected": sel, "reviewer": rev, "excluded": excluded, "evidence_refs": d.get("evidence_refs", []),
                     "synthetic": (sel.get("adapter") == "mock") if sel else None, "decision": d})
    groups = []
    seen = set()
    for status in (*STATUS_ORDER, *[c["epistemic_status"] for c in doc["claims"]]):
        if status in seen:
            continue
        seen.add(status)
        items = [c for c in doc["claims"] if c["epistemic_status"] == status]
        if items:
            groups.append({"status": status, "label": STATUS_LABELS.get(status, status), "claims": items})
    tabs = [("frage", "Frage & Protokoll", None), ("aussagen", "Aussagen", len(doc["claims"])), ("quellen", "Quellen", len(doc["sources"])),
            ("laeufe", "Läufe", len(runs)), ("fassungen", "Fassungen", len(artifacts))]
    if tab not in {t[0] for t in tabs}:
        tab = "frage"
    ws = db.get(Workspace, actor.workspace_id)
    workflows = db.execute(select(WorkflowRun).where(WorkflowRun.project_id == project_id).order_by(WorkflowRun.created_at)).scalars().all()
    step = next_step(stopped=bool(ws.stopped), mandate_state=doc["mandate"]["state"], projects=1, tasks=doc["protocol"], runs=doc["runs"],
                     reviews=doc["reviews"], artifacts=artifacts, workflows=[{"status": None} for _ in workflows] if not doc["runs"] else [])
    return _render("dossier.html", _ctx(request, db, actor, "dossiers", doc=doc, artifacts=artifacts, runs=runs, groups=groups, tabs=tabs, tab=tab,
                                        institute=institute, last_run=runs[-1] if runs else None, step=step, workflows=workflows,
                                        mandate_version_id=doc["mandate"]["mandate_version_id"]))


# ---------------------------------------------------------------- research order journey (Mandat -> Entscheid -> Projekt/Aufgabe -> Ablauf -> Ergebnis)

@router.get("/auftrag/neu", response_class=HTMLResponse)
def auftrag_neu(request: Request, actor: Actor = Depends(web_actor), db: Session = Depends(get_db)):
    return _render("auftrag_neu.html", _ctx(request, db, actor, "dossiers", defaults=MANDATE_DEFAULTS))


@router.get("/auftrag/{mandate_version_id}", response_class=HTMLResponse)
def auftrag(mandate_version_id: str, request: Request, actor: Actor = Depends(web_actor), db: Session = Depends(get_db)):
    mandate = governance.get_mandate(mandate_version_id, actor=actor, db=db)
    ws = db.get(Workspace, actor.workspace_id)
    projects = [p for p in governance_list_projects(db, actor) if p["mandate_version_id"] == mandate_version_id]
    project = projects[-1] if projects else None
    tasks, runs, reviews, artifacts, workflows, synthetic = [], [], [], [], [], False
    if project:
        doc = services_dossier.dossier_document(db, actor=actor, project_id=project["project_id"])
        tasks, runs, reviews = doc["protocol"], doc["runs"], doc["reviews"]
        synthetic = doc["declarations"]["synthetic_content"]
        artifacts = services_dossier.project_artifacts(db, actor=actor, project_id=project["project_id"])
        workflows = db.execute(select(WorkflowRun).where(WorkflowRun.project_id == project["project_id"]).order_by(WorkflowRun.created_at)).scalars().all()
    step = next_step(stopped=bool(ws.stopped), mandate_state=mandate["state"], projects=len(projects), tasks=tasks, runs=runs, reviews=reviews,
                     artifacts=artifacts, workflows=[{"status": None} for _ in workflows] if not runs else [])
    labels = [("Mandat", mandate["state"] == "approved"), ("Projekt und Aufgabe", bool(tasks)), ("Ablauf", bool(runs) or bool(workflows)),
              ("Gegenprüfung", bool(reviews)), ("Dossier", any(a["kind"] == "dossier" for a in artifacts)),
              ("Ergebnisfreigabe", step["code"] == "approved")]
    progress = {"steps": [{"label": lbl, "done": done} for lbl, done in labels], "done": sum(1 for _, d in labels if d), "total": len(labels)}
    from fastapi.encoders import jsonable_encoder

    profiles = jsonable_encoder([a for a in services_research.inventory(db, actor.workspace_id)["agents"] if a["operating_status"] != "archived"])
    policies = governance.list_policies(actor=actor, db=db)
    policy_revision = next((p["revision"] for p in policies if p["state"] == "active"), "")
    institutes = db.execute(select(Institute).where(Institute.workspace_id == actor.workspace_id).order_by(Institute.sort_order)).scalars().all()
    dossiers = [a for a in artifacts if a["kind"] == "dossier"]
    return _render("auftrag.html", _ctx(request, db, actor, "dossiers", mandate=mandate, project=project, task=tasks[-1] if tasks else None, runs=runs,
                                        reviews=reviews, artifacts=artifacts, workflows=workflows, workflow=workflows[-1] if workflows else None, step=step,
                                        progress=progress, profiles=profiles, policy_revision=policy_revision, institutes=institutes, synthetic=synthetic,
                                        task_defaults=TASK_DEFAULTS, latest_artifact=dossiers[-1] if dossiers else None))


def governance_list_projects(db: Session, actor: Actor) -> list[dict]:
    from ..routers import research

    return research.list_projects(institute_id=None, actor=actor, db=db)


@router.get("/ergebnisse", response_class=HTMLResponse)
def ergebnisse(request: Request, actor: Actor = Depends(web_actor), db: Session = Depends(get_db)):
    institutes = {i.id: i.name for i in db.execute(select(Institute).where(Institute.workspace_id == actor.workspace_id)).scalars().all()}
    rows = []
    for p in db.execute(select(Project).where(Project.workspace_id == actor.workspace_id)).scalars().all():
        for a in services_dossier.project_artifacts(db, actor=actor, project_id=p.id):
            art = db.get(ArtifactVersion, a["artifact_version_id"])
            rows.append({**a, "project_id": p.id, "project_title": p.title, "institute": institutes.get(p.institute_id, p.institute_id),
                         "producer": art.producer_canonical_model_id if art else "–"})
    rows.sort(key=lambda r: r["created_at"], reverse=True)
    return _render("ergebnisse.html", _ctx(request, db, actor, "ergebnisse", rows=rows))


@router.get("/artefakte/{artifact_id}", response_class=HTMLResponse)
def artefakt(artifact_id: str, request: Request, actor: Actor = Depends(web_actor), db: Session = Depends(get_db)):
    art = db.get(ArtifactVersion, artifact_id)
    if art is None or art.workspace_id != actor.workspace_id:
        raise CampusError(ErrorCode.NOT_FOUND, "Fassung nicht gefunden")
    project = db.get(Project, art.project_id) if art.project_id else None
    entry = next((e for e in services_dossier.project_artifacts(db, actor=actor, project_id=art.project_id) if e["artifact_version_id"] == art.id), None) \
        if art.project_id else None
    entry = entry or {"approval": None, "releases": []}
    ap = entry.get("approval")
    entry["approval_decidable"] = not (ap and ap.get("valid_for_current") and not ap.get("basis_withdrawn_after_approval"))
    return _render("artefakt.html", _ctx(request, db, actor, "ergebnisse", artifact=art, project=project, entry=entry,
                                         artifact_json=json.dumps(art.content, indent=2, ensure_ascii=False, default=str)))


# ---------------------------------------------------------------- register, models, review, library, operations

@router.get("/register", response_class=HTMLResponse)
def register(request: Request, actor: Actor = Depends(web_actor), db: Session = Depends(get_db)):
    inv = services_research.inventory(db, actor.workspace_id)
    institutes = {i.id: i.name for i in db.execute(select(Institute).where(Institute.workspace_id == actor.workspace_id)).scalars().all()}
    return _render("register.html", _ctx(request, db, actor, "register", inventory=inv, institutes=institutes))


@router.get("/register/{ref}", response_class=HTMLResponse)
def rationale(ref: str, request: Request, actor: Actor = Depends(web_actor), db: Session = Depends(get_db)):
    data = services_factory.rationale(db, actor=actor, ref=ref)
    return _render("rationale.html", _ctx(request, db, actor, "register", ref=ref, data=data, data_json=json.dumps(data, indent=2, ensure_ascii=False, default=str)))


@router.get("/modelle", response_class=HTMLResponse)
def modelle(request: Request, actor: Actor = Depends(web_actor), db: Session = Depends(get_db)):
    catalog = governance.catalog_status(actor=actor, db=db)
    quals = governance.list_qualifications(actor=actor, db=db)
    policies = governance.list_policies(actor=actor, db=db)
    return _render("modelle.html", _ctx(request, db, actor, "modelle", catalog=catalog, qualifications=quals, policies=policies))


@router.get("/pruefung", response_class=HTMLResponse)
def pruefung(request: Request, actor: Actor = Depends(web_actor), db: Session = Depends(get_db)):
    mandates = governance.list_mandates(actor=actor, db=db)
    open_ = [m for m in mandates if m["state"] == "draft"]
    decisions = db.execute(select(HumanDecision).where(HumanDecision.workspace_id == actor.workspace_id).order_by(HumanDecision.decided_at.desc()).limit(30)).scalars().all()
    users = {u.id: u.display_name for u in db.execute(select(User).where(User.workspace_id == actor.workspace_id)).scalars().all()}
    return _render("pruefung.html", _ctx(request, db, actor, "pruefung", open_mandates=open_, mandates=mandates, decisions=decisions, users=users))


@router.get("/bibliothek", response_class=HTMLResponse)
def bibliothek(request: Request, actor: Actor = Depends(web_actor), db: Session = Depends(get_db)):
    projects = db.execute(select(Project).where(Project.workspace_id == actor.workspace_id).order_by(Project.created_at)).scalars().all()
    rows = []
    for p in projects:
        for s in services_sources.project_sources(db, actor=actor, project_id=p.id):
            rows.append({**s, "project_id": p.id, "project_title": p.title})
    return _render("bibliothek.html", _ctx(request, db, actor, "bibliothek", rows=rows))


@router.get("/betrieb", response_class=HTMLResponse)
def betrieb(request: Request, actor: Actor = Depends(web_actor), db: Session = Depends(get_db)):
    ready = json.loads(health.ready(request).body)
    catalog = governance.catalog_status(actor=actor, db=db)
    sandbox = services_experiments.status(db, workspace_id=actor.workspace_id, runner=getattr(request.app.state, "sandbox_runner", None),
                                          capability_level=get_settings().capability_level)
    pilots = [services_pilot.pilot_view(db, p) for p in db.execute(select(PilotStudy).where(PilotStudy.workspace_id == actor.workspace_id)
                                                                  .order_by(PilotStudy.created_at)).scalars().all()]
    tree = budget_service.tree(db, actor.workspace_id)
    unknown = db.execute(select(func.count()).select_from(AuditEvent).where(AuditEvent.workspace_id == actor.workspace_id,
                                                                            AuditEvent.payload["end_reason"].astext == "outcome_unknown")).scalar() or 0
    return _render("betrieb.html", _ctx(request, db, actor, "betrieb", ready=ready, catalog=catalog, sandbox=sandbox, pilots=pilots, tree=tree, unknown=unknown))
