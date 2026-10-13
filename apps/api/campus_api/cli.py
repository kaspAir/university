"""Operator CLI: bootstrap workspace, users, policy and catalog drafts. Runs on the server, audited as actor type system."""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import uuid
from pathlib import Path

import yaml

from campus_domain.authz import Role
from campus_domain.errors import CampusError
from campus_infra.audit import Actor
from campus_infra.db import make_engine, make_session_factory

from . import services_governance, services_identity
from .settings import get_settings


def _system_actor(workspace_id: str) -> Actor:
    return Actor(actor_type="system", actor_id=f"cli:{getpass.getuser()}", workspace_id=workspace_id, roles=frozenset(Role))


def _load(path: str) -> dict:
    text = Path(path).read_text(encoding="utf-8")
    return yaml.safe_load(text) if path.endswith((".yaml", ".yml")) else json.loads(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="campus-admin")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init-workspace", help="Workspace mit den fünf Instituten anlegen")
    p.add_argument("--id", required=True)
    p.add_argument("--name", required=True)

    p = sub.add_parser("create-user", help="Benutzer anlegen; Passwort aus CAMPUS_ADMIN_PASSWORD oder Eingabe")
    p.add_argument("--workspace", required=True)
    p.add_argument("--username", required=True)
    p.add_argument("--display-name", required=True)
    p.add_argument("--roles", required=True, help="Kommagetrennt, z. B. research_director,operator")

    p = sub.add_parser("import-policy", help="Routing-Policy als Entwurf importieren")
    p.add_argument("--workspace", required=True)
    p.add_argument("path")

    p = sub.add_parser("import-catalog", help="Modellkatalog als Entwurf importieren")
    p.add_argument("--workspace", required=True)
    p.add_argument("path")

    p = sub.add_parser("probe-provider", help="Discovery über ein registriertes Katalog-Deployment (Adapter, Endpunkt, Schlüsselreferenz aus dem Katalog)")
    p.add_argument("--workspace", required=True)
    p.add_argument("--deployment-id", required=True)

    p = sub.add_parser("probe-invocation", help="EIN kleiner echter Aufruf über das Gateway (Journal, Nutzungswerte, Modellkennung, "
                                                "strukturierte Ausgabe); kostet Geld, Obergrenze Pflicht; keine Forschung")
    p.add_argument("--workspace", required=True)
    p.add_argument("--deployment-id", required=True)
    p.add_argument("--max-cost", required=True, help="Obergrenze für diese eine Probe, z. B. 0.05")
    p.add_argument("--currency", default="CHF")

    args = parser.parse_args(argv)
    settings = get_settings()
    factory = make_session_factory(make_engine(settings.database_url))
    correlation = f"cli-{uuid.uuid4().hex}"
    with factory() as db:
        if args.cmd == "init-workspace":
            ws = services_identity.init_workspace(db, workspace_id=args.id, name=args.name, capability_level=settings.capability_level)
            db.commit()
            print(f"Workspace {ws.id} angelegt ({settings.capability_level}), fünf Institute gesetzt.")
        elif args.cmd == "create-user":
            password = os.environ.get("CAMPUS_ADMIN_PASSWORD") or getpass.getpass("Passwort (min. 12 Zeichen): ")
            user = services_identity.create_user(
                db, actor=_system_actor(args.workspace), workspace_id=args.workspace, username=args.username,
                display_name=args.display_name, password=password, roles=args.roles.split(","), correlation_id=correlation,
            )
            db.commit()
            print(f"Benutzer {user.username} ({user.id}) angelegt mit Rollen {args.roles}.")
        elif args.cmd == "import-policy":
            row = services_governance.import_policy(db, actor=_system_actor(args.workspace), document=_load(args.path),
                                                    correlation_id=correlation)
            db.commit()
            print(f"Policy-Entwurf {row.id} ({row.revision}) importiert. Aktivierung über die API durch die Forschungsleitung.")
        elif args.cmd == "probe-provider":
            from . import services_research

            result = services_research.discover_models(db, actor=_system_actor(args.workspace), deployment_id=args.deployment_id)
            print(json.dumps(result, indent=1, ensure_ascii=False, default=str))
        elif args.cmd == "probe-invocation":
            from . import services_probe

            try:
                result = services_probe.probe_deployment(db, actor=_system_actor(args.workspace), deployment_id=args.deployment_id,
                                                         max_cost=args.max_cost, currency=args.currency, correlation_id=correlation)
            except CampusError as err:  # a refused or failed probe is a result too: the error object, journaled and audited, no traceback
                print(json.dumps({"probe": "failed", **err.to_object("cli")}, indent=1, ensure_ascii=False, default=str))
                return 2
            print(json.dumps(result, indent=1, ensure_ascii=False, default=str))
        elif args.cmd == "import-catalog":
            row = services_governance.import_catalog(db, actor=_system_actor(args.workspace), document=_load(args.path),
                                                     correlation_id=correlation)
            db.commit()
            print(f"Katalog-Entwurf {row.id} ({row.revision}) importiert. Aktivierung über die API (Betrieb).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
