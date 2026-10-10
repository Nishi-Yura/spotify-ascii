"""Start spotify-ascii when you sign in to Windows: a shortcut to run.bat in
the Startup folder (the window starts minimised). autostart.bat and the tray
menu switch it on / off.

The shortcut is made by PowerShell (WScript.Shell); the paths travel in
environment variables, so any folder name works (quotes, spaces, Japanese).
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STARTUP = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
LNK = os.path.join(STARTUP, "spotify-ascii.lnk")
OLD = [os.path.join(STARTUP, "spotify-ascii wallpaper.lnk")]   # made by older versions

PS = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:SA_LNK); "
      "$s.TargetPath = $env:SA_TARGET; $s.WorkingDirectory = $env:SA_DIR; "
      "$s.WindowStyle = 7; $s.Save()")


def enabled():
    return os.path.exists(LNK)


def set_enabled(on):
    """-> True if the shortcut is now as asked."""
    for p in OLD:
        try:
            os.remove(p)
        except OSError:
            pass
    if not on:
        try:
            os.remove(LNK)
        except OSError:
            pass
        return not enabled()
    env = dict(os.environ, SA_LNK=LNK, SA_TARGET=os.path.join(HERE, "run.bat"), SA_DIR=HERE)
    try:
        os.makedirs(STARTUP, exist_ok=True)
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", PS],
                       env=env, capture_output=True, timeout=30,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        pass
    return enabled()


def main():
    on = not enabled()
    if set_enabled(on) != on:
        print(" Could not change the shortcut in %s" % STARTUP)
        return 1
    if on:
        print(" Autostart is now ON. spotify-ascii will start minimised when you sign in.")
    else:
        print(" Autostart is now OFF. spotify-ascii will no longer start when you sign in.")
    print(" Run this file again to switch it back.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
