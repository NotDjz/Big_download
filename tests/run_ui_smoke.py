"""Lance Flask + Chrome headless, execute tests/ui_smoke.mjs, nettoie.

    py tests/run_ui_smoke.py

Prerequis : Node 22+ (WebSocket global natif) et Chrome ou Edge installe. Ne touche a aucun
fichier du depot ; le profil Chrome est jetable et cree dans le dossier temp.
"""
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CDP_PORT = 9223
CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
)


def _busy(port):
    """Vrai si quelque chose ecoute deja sur ce port."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def _wait(port, timeout=25):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                return True
        except OSError:
            time.sleep(0.15)
    return False


def main():
    sys.path.insert(0, str(ROOT))
    import app  # noqa: E402  (le serveur est le sujet du test)

    # Sans ces gardes, un BIG DL deja lance ou un Chrome oublie repondrait a
    # l attente et la suite testerait un autre processus que celui qu elle croit,
    # en rapportant tout vert pour du code jamais charge.
    if _busy(app.PORT):
        print(f"ECHEC : le port {app.PORT} est deja pris. Ferme BIG DL avant de lancer le test.")
        return 2
    if _busy(CDP_PORT):
        print(f"ECHEC : le port {CDP_PORT} est deja pris (Chrome oublie ?).")
        return 2

    threading.Thread(
        target=lambda: app.app.run(host="127.0.0.1", port=app.PORT, threaded=True),
        daemon=True,
    ).start()
    if not _wait(app.PORT):
        print("ECHEC : le serveur Flask n'a pas demarre")
        return 2

    chrome = next((c for c in CHROME_CANDIDATES if Path(c).exists()), None)
    if not chrome:
        print("ECHEC : ni Chrome ni Edge trouve")
        return 2

    profile = tempfile.mkdtemp(prefix="bigdl-uismoke-")
    proc = subprocess.Popen(
        [
            chrome, "--headless=new", f"--remote-debugging-port={CDP_PORT}",
            f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
            "--autoplay-policy=no-user-gesture-required", "--disable-gpu", "--mute-audio",
        ],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **app._SUBPROCESS_FLAGS,
    )
    try:
        if not _wait(CDP_PORT):
            print("ECHEC : Chrome n'expose pas CDP")
            return 2
        env = {**os.environ, "CDP_PORT": str(CDP_PORT), "APP_URL": f"http://localhost:{app.PORT}"}
        try:
            return subprocess.call(
                ["node", str(ROOT / "tests" / "ui_smoke.mjs")], env=env, timeout=300)
        except FileNotFoundError:
            print("ECHEC : node introuvable dans le PATH (Node 22+ requis)")
            return 2
        except subprocess.TimeoutExpired:
            print("ECHEC : le test a depasse 5 minutes")
            return 2
    finally:
        proc.kill()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        shutil.rmtree(profile, ignore_errors=True)
        # Le test televerse une video pour la couper, et une coupe annulee garde
        # volontairement sa source : sans ce nettoyage, chaque execution laisse
        # plusieurs centaines de Mo dans temp_uploads/ jusqu'au balayage horaire.
        # Sans risque pour un travail en cours : la fonction refuse de demarrer
        # quand le port de l'application est deja pris.
        for leftover in app.TEMP_FOLDER.glob('*'):
            if leftover.is_file():
                try:
                    leftover.unlink()
                except OSError:
                    # missing_ok ne couvre pas un verrou Windows : FFmpeg peut
                    # tenir encore le fichier quelques instants. Le balayage
                    # horaire de l'application s'en chargera.
                    pass


if __name__ == "__main__":
    sys.exit(main())
