"""Linha de comando: `chipcollector serve | share | create-user | set-password | export | demo`."""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path


def _serve(a):
    import uvicorn

    uvicorn.run("chipcollector.app:create_app", factory=True, host=a.host, port=a.port, reload=a.reload,
                workers=1 if a.reload else a.workers)


def _share(a):
    from .share import run

    sys.exit(run(port=a.port, allow_weak=a.allow_demo_password))


def _set_password(a):
    from sqlmodel import Session, select

    from .auth import hash_password
    from .db import User, get_engine

    with Session(get_engine()) as s:
        u = s.exec(select(User).where(User.username == a.username.strip().lower())).first()
        if not u:
            sys.exit(f"Usuário '{a.username}' não encontrado.")
        pwd = a.password or getpass.getpass(f"Nova senha para {u.username}: ")
        if len(pwd) < 6:
            sys.exit("A senha precisa ter pelo menos 6 caracteres.")
        u.password_hash = hash_password(pwd)
        s.add(u)
        s.commit()
        print(f"Senha de {u.username} alterada.")


def _create_user(a):
    from sqlmodel import Session

    from .auth import create_user
    from .db import get_engine

    pwd = a.password or getpass.getpass("Senha: ")
    with Session(get_engine()) as s:
        u = create_user(s, a.username, pwd, full_name=a.full_name, email=a.email, is_admin=a.admin)
        print(f"Usuário criado: {u.username} (admin={u.is_admin})")


def _export(a):
    from sqlmodel import Session

    from .db import Project, get_engine
    from .export import build_dataset

    with Session(get_engine()) as s:
        p = s.get(Project, a.project)
        if not p:
            sys.exit("Projeto não encontrado.")
        opts = {"source_ids": a.source or [], "statuses": a.status, "include_images": not a.no_images}
        z = build_dataset(s, p, opts, Path(a.out), lambda pr, m: print(f"[{pr:4.0%}] {m}", end="\r"))
        print(f"\nArquivo: {z}")
        if a.drive:
            from .drive import upload
            fid, _ = upload(z)
            print(f"Enviado ao Google Drive: {fid}")


def _demo(a):
    from .demo import create_demo, seed_demo

    if a.seed:
        seed_demo(Path(a.out), a.user, a.password)
    else:
        create_demo(Path(a.out))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="chipcollector", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="inicia o servidor web")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--workers", type=int, default=1)
    s.add_argument("--reload", action="store_true")
    s.set_defaults(fn=_serve)

    sh = sub.add_parser("share", help="roda no seu computador e cria um endereço público temporário (Cloudflare)")
    sh.add_argument("--port", type=int, default=8000)
    sh.add_argument("--allow-demo-password", action="store_true",
                    help="compartilha mesmo se alguma conta ainda usar a senha de demonstração")
    sh.set_defaults(fn=_share)

    sp = sub.add_parser("set-password", help="define a senha de um usuário existente")
    sp.add_argument("username")
    sp.add_argument("--password")
    sp.set_defaults(fn=_set_password)

    u = sub.add_parser("create-user", help="cria usuário (use --admin para administrador)")
    u.add_argument("username")
    u.add_argument("--password")
    u.add_argument("--full-name", default="")
    u.add_argument("--email", default="")
    u.add_argument("--admin", action="store_true", default=None)
    u.set_defaults(fn=_create_user)

    e = sub.add_parser("export", help="exporta amostras de um projeto para uma pasta local")
    e.add_argument("project", type=int)
    e.add_argument("--out", default="./exports")
    e.add_argument("--source", action="append", help="id da fonte de imagem (repetível)")
    e.add_argument("--status", nargs="+", default=["done"])
    e.add_argument("--no-images", action="store_true")
    e.add_argument("--drive", action="store_true", help="enviar também ao Drive (conta de serviço)")
    e.set_defaults(fn=_export)

    d = sub.add_parser("demo", help="gera dados sintéticos (camada de referência + mosaico) para testar offline")
    d.add_argument("--out", default="./demo_data")
    d.add_argument("--seed", action="store_true", help="cria também usuário admin e projetos de demonstração")
    d.add_argument("--user", default="admin")
    d.add_argument("--password", default="admin123")
    d.set_defaults(fn=_demo)

    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
