"""Self-update from GitHub Releases.

At start-up the newest release of the repository is looked up (at most once an
hour). If it differs from the installed one, its source is downloaded, the
program files are replaced, new libraries are installed and the program asks
run.bat to start again (exit code RESTART). Settings, per-track choices, your
own clips (media/) and .venv are never touched.

Skipped when the folder is a git checkout (use git pull there), when
--no-update is given, or when "auto_update": false is set in app_settings.json.
Any network problem is ignored: the installed version simply starts.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

REPO = "Nishi-Yura/spotify-ascii"
RESTART = 10                       # exit code run.bat answers by starting again
HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, ".update.json")
CHECK_EVERY = 3600                 # seconds between looks at GitHub
# what an update may write: program files only (never settings, media, .venv, *.bat)
TOP_FILES = {"README.md", "LICENSE", "requirements.txt", "requirements-video.txt"}
TOP_DIRS = {"assets"}


def _get(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": "spotify-ascii-updater",
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _load():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(st):
    try:
        with open(STATE, "w", encoding="utf-8") as f:
            json.dump(st, f)
    except Exception:
        pass


def _wanted(rel):
    """Is this path (relative to the project folder, '/' separated) updatable?"""
    parts = rel.split("/")
    if not rel or ".." in parts or rel.startswith("/") or ":" in rel:
        return False
    if len(parts) == 1:
        return rel in TOP_FILES or rel.endswith(".py")
    return parts[0] in TOP_DIRS or rel == "media/README.txt"


def _apply(data):
    """Copy the program files out of the release zip over this folder.
    -> True if requirements.txt changed."""
    req = os.path.join(HERE, "requirements.txt")
    try:
        with open(req, "rb") as f:
            old_req = f.read()
    except OSError:
        old_req = b""
    new_req = old_req
    with zipfile.ZipFile(io.BytesIO(data)) as z, tempfile.TemporaryDirectory() as tmp:
        names = [n for n in z.namelist() if not n.endswith("/")]
        top = names[0].split("/")[0] + "/" if names else ""
        staged = []
        for n in names:                              # stage everything first: all or nothing
            rel = n[len(top):] if n.startswith(top) else ""
            if not _wanted(rel):
                continue
            dst = os.path.join(tmp, *rel.split("/"))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as f:
                f.write(z.read(n))
            staged.append((rel, dst))
            if rel == "requirements.txt":
                new_req = open(dst, "rb").read()
        if not any(r.endswith("main.py") for r, _ in staged):
            raise RuntimeError("release has no main.py")
        for rel, src in staged:
            dst = os.path.join(HERE, *rel.split("/"))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
    return new_req != old_req


def run(force=False):
    """Update if a newer release exists. Returns True when files were replaced
    (the caller should exit with RESTART)."""
    if os.path.exists(os.path.join(HERE, ".git")):
        return False
    st = _load()
    now = time.time()
    if not force and now - st.get("checked", 0) < CHECK_EVERY:
        return False
    try:
        info = json.loads(_get("https://api.github.com/repos/%s/releases/latest" % REPO, 4))
        tag, url = info["tag_name"], info["zipball_url"]
    except Exception:
        return False                                 # offline / rate limited: try next time
    st["checked"] = now
    if st.get("tag") == tag:
        _save(st)
        return False
    print("spotify-ascii: 新しいバージョン %s に更新しています…" % tag, flush=True)
    try:
        if _apply(_get(url, 60)):
            print("spotify-ascii: ライブラリを更新しています…", flush=True)
            subprocess.call([sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
                             "-q", "-r", os.path.join(HERE, "requirements.txt")])
    except Exception as e:
        print("spotify-ascii: 更新できませんでした（今のバージョンで起動します）: %s" % e, flush=True)
        _save(st)
        return False
    st["tag"] = tag
    _save(st)
    print("spotify-ascii: %s に更新しました。再起動します。" % tag, flush=True)
    return True
