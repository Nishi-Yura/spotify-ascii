"""Wait for Spotify to start, open the visualiser in its own window, and let
it close again after Spotify quits (run.bat --quit-idle). watch.bat runs this;
put a shortcut to watch.bat in shell:startup to have it waiting all the time.
"""
import os
import shutil
import subprocess
import time

HERE = os.path.dirname(os.path.abspath(__file__))
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def spotify_running():
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Spotify.exe", "/NH"],
                             capture_output=True, text=True, timeout=15, creationflags=NO_WINDOW).stdout
    except Exception:
        return False
    return "spotify.exe" in out.lower()


def open_visualiser():
    args = ["cmd", "/c", "run.bat", "--quit-idle", "30"]
    if shutil.which("wt"):
        subprocess.Popen(["wt", "-w", "new", "-d", HERE, "--title", "spotify ascii"] + args, cwd=HERE)
    else:
        subprocess.Popen(args, cwd=HERE, creationflags=subprocess.CREATE_NEW_CONSOLE)


def main():
    print(" spotify-ascii: waiting for Spotify (close this window to stop) ...", flush=True)
    while True:
        while not spotify_running():
            time.sleep(5)
        open_visualiser()
        while spotify_running():                  # then wait until Spotify has quit
            time.sleep(5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
