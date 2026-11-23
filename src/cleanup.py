import traceback
from . import drive
from .common import notify


def main():
    try:
        deleted = drive.cleanup()
    except Exception as ex:
        err_msg = f"Drive cleanup failed: {ex}\n{traceback.format_exc()[-800:]}"
        notify(err_msg)
        raise

    if deleted:
        notify(f"Drive cleanup: {deleted} file(s) older than retention deleted")
    else:
        print("nothing to delete")


if __name__ == "__main__":
    main()
