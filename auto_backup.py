import os
import subprocess
from datetime import datetime

BACKUP_FILE = "movies.json"

def auto_backup():
    if not os.path.exists(BACKUP_FILE):
        print("Backup fayl topilmadi")
        return

    try:
        subprocess.run(["git", "add", BACKUP_FILE], check=True)
        subprocess.run(
            ["git", "commit", "-m", f"auto backup {datetime.now():%Y-%m-%d %H:%M}"],
            check=True
        )
        subprocess.run(["git", "push"], check=True)
        print("Auto backup muvaffaqiyatli")
    except subprocess.CalledProcessError as e:
        print("Auto backup xatosi:", e)
