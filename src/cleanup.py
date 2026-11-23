from . import drive
from .common import notify

if __name__ == "__main__":
    n = drive.cleanup()
    notify(f"Drive cleanup: {n} file(s) older than retention deleted") if n else print("nothing to delete")
    
