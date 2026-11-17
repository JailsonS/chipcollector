"""`chipcollector share`: roda a aplicação no seu computador e publica um endereço HTTPS temporário
com um Cloudflare Quick Tunnel (trycloudflare.com), sem conta e sem custo.

Requer o programa `cloudflared` instalado. O endereço muda a cada execução e deixa de funcionar
quando o comando é encerrado (Ctrl+C).
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request

URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
DEMO_PASSWORDS = ("admin123",)

INSTALL_HELP = {
    "Darwin": "brew install cloudflared",
    "Windows": "winget install --id Cloudflare.cloudflared",
    "Linux": "veja https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/downloads/",
}


def _weak_accounts() -> list[str]:
    """Contas ativas que ainda usam a senha de demonstração."""
    from sqlmodel import Session, select

    from .auth import verify_password
    from .db import User, get_engine

    with Session(get_engine()) as s:
        return [u.username for u in s.exec(select(User).where(User.is_active == True)).all()  # noqa: E712
                if any(verify_password(p, u.password_hash) for p in DEMO_PASSWORDS)]


def _wait_http(url: str, timeout: float = 60) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status < 500:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def _box(lines: list[str]) -> str:
    w = max(len(x) for x in lines)
    bar = "+" + "-" * (w + 2) + "+"
    return "\n".join([bar, *[f"| {x.ljust(w)} |" for x in lines], bar])


def run(port: int = 8000, allow_weak: bool = False) -> int:
    exe = shutil.which("cloudflared")
    if not exe:
        how = INSTALL_HELP.get(platform.system(), INSTALL_HELP["Linux"])
        print("O programa 'cloudflared' não foi encontrado.\nInstale com:  " + how +
              "\nDepois rode de novo:  chipcollector share", file=sys.stderr)
        return 2

    weak = _weak_accounts()
    if weak:
        msg = (f"Atenção: a(s) conta(s) {', '.join(weak)} ainda usa(m) a senha de demonstração.\n"
               f"Qualquer pessoa com o endereço poderia entrar. Troque antes de compartilhar:\n"
               f"  chipcollector set-password {weak[0]}")
        if not allow_weak:
            print(msg + "\n(ou rode com --allow-demo-password para ignorar este aviso)", file=sys.stderr)
            return 3
        print(msg, file=sys.stderr)

    env = dict(os.environ)
    # Ninguém de fora cria conta sozinho enquanto o túnel estiver aberto, a menos que você peça.
    env.setdefault("CHIPS_ALLOW_SIGNUP", "false")
    local = f"http://127.0.0.1:{port}"
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "chipcollector.app:create_app", "--factory",
         "--host", "127.0.0.1", "--port", str(port), "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1"],
        env=env)
    if not _wait_http(local + "/api/config"):
        print("A aplicação não respondeu. Veja as mensagens acima.", file=sys.stderr)
        server.terminate()
        return 1

    tunnel = subprocess.Popen([exe, "tunnel", "--no-autoupdate", "--url", local],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    found = threading.Event()

    def pump():
        for line in tunnel.stdout:  # type: ignore[union-attr]
            m = URL_RE.search(line)
            if m and not found.is_set():
                found.set()
                url = m.group(0)
                print("\n" + _box([
                    "ChipCollector publicado para teste",
                    "",
                    f"Endereço público:  {url}",
                    f"Neste computador:  http://localhost:{port}",
                    "",
                    "O endereço muda a cada execução e para de funcionar",
                    "quando você fechar este terminal ou apertar Ctrl+C.",
                    "Cadastro aberto ao público: " + ("sim" if env["CHIPS_ALLOW_SIGNUP"].lower() in ("1", "true", "yes", "sim") else
                                                     "não (crie as contas em Usuários)"),
                    "Uploads acima de 100 MB: envie pelo endereço local.",
                ]) + "\n", flush=True)
            elif re.search(r"\b(ERR|error)\b", line, re.I) and not found.is_set():
                print("[cloudflared] " + line.rstrip(), file=sys.stderr)

    threading.Thread(target=pump, daemon=True).start()

    def stop(*_):
        for p in (tunnel, server):
            if p.poll() is None:
                p.terminate()

    signal.signal(signal.SIGINT, stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, stop)

    if not found.wait(45):
        print("O túnel não informou o endereço em 45 s. Verifique sua conexão e tente de novo.", file=sys.stderr)
    try:
        while server.poll() is None and tunnel.poll() is None:
            time.sleep(0.5)
    finally:
        stop()
        for p in (tunnel, server):
            try:
                p.wait(10)
            except Exception:
                p.kill()
    print("Compartilhamento encerrado.")
    return 0
