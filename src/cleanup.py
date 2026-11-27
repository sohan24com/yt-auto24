from . import drive
from .common import notify

if __name__ == "__main__":
    n = drive.cleanup()
    notify(f"Drive cleanup: {n} expired file(s) moved to recoverable trash") if n else print("nothing expired")
