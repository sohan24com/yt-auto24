"""Local post-production tool detection; adapters are reported only when they are real."""
import os
import shutil
import subprocess
from pathlib import Path


def _version(executable, timeout=8):
    if not executable:
        return None
    try:
        result = subprocess.run([str(executable), "-version"], capture_output=True, text=True,
                                timeout=timeout)
        if result.returncode == 0:
            return (result.stdout or result.stderr).splitlines()[0].strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def _portable_bin(repository_root):
    root = Path(repository_root) / ".work" / "ffmpeg"
    if not root.exists():
        return None
    bins = sorted(root.glob("*/bin"), key=lambda p: p.as_posix(), reverse=True)
    return next((folder for folder in bins if (folder / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")).is_file()), None)


def detect_media_tools(repository_root=None, prepare_path=True):
    """Detect ffmpeg/ffprobe, prefer a verified local portable build, report filters and versions."""
    root = Path(repository_root).resolve() if repository_root else Path(__file__).resolve().parent.parent
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    portable = _portable_bin(root)
    if portable and (not ffmpeg or not ffprobe):
        if prepare_path:
            os.environ["PATH"] = str(portable) + os.pathsep + os.environ.get("PATH", "")
            ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    versions = {"ffmpeg": _version(ffmpeg), "ffprobe": _version(ffprobe)}
    filters = set()
    if versions["ffmpeg"]:
        try:
            result = subprocess.run([ffmpeg, "-hide_banner", "-filters"], capture_output=True,
                                    text=True, timeout=10)
            for name in ("loudnorm", "sidechaincompress", "acompressor", "alimiter",
                         "afftdn", "astats", "silencedetect", "ametadata", "blackdetect", "freezedetect"):
                if result.returncode == 0 and name in result.stdout:
                    filters.add(name)
        except (OSError, subprocess.TimeoutExpired):
            pass
    required = {"loudnorm", "sidechaincompress", "acompressor", "alimiter", "astats",
                "silencedetect", "ametadata", "blackdetect", "freezedetect"}
    return {"ready": bool(versions["ffmpeg"] and versions["ffprobe"] and required <= filters),
            "ffmpeg": {"path": ffmpeg, "version": versions["ffmpeg"]},
            "ffprobe": {"path": ffprobe, "version": versions["ffprobe"]},
            "filters": sorted(filters), "missing_filters": sorted(required - filters),
            "portable_build": str(portable) if portable else None}


def software_inventory():
    """Report optional editor software without pretending unsupported adapters are integrated."""
    candidates = {
        "blender": [shutil.which("blender"), Path("C:/Program Files/Blender Foundation")],
        "resolve": [shutil.which("resolve"), Path("C:/Program Files/Blackmagic Design/DaVinci Resolve/Resolve.exe")],
        "after_effects": [shutil.which("AfterFX"), Path("C:/Program Files/Adobe")],
    }
    found = {}
    for name, options in candidates.items():
        executable = next((Path(x) for x in options if x and Path(x).is_file()), None)
        if executable is None and name == "blender":
            roots = [Path("C:/Program Files/Blender Foundation"), Path("C:/Program Files/Blender")]
            executable = next((item for base in roots if base.exists()
                               for item in base.glob("Blender */blender.exe")), None)
        if executable is None and name == "after_effects":
            base = Path("C:/Program Files/Adobe")
            executable = next((item for item in base.glob("Adobe After Effects */Support Files/AfterFX.exe")
                               if base.exists()), None)
        found[name] = {"detected": bool(executable), "path": str(executable) if executable else None,
                       "adapter": "not integrated"}
    return found
