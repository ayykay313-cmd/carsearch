"""Install the OS schedules:
- a daily 1:00pm Dubai-time run (missed runs while asleep catch up on wake), and
- a Telegram sync every 15 minutes that applies your "Not interested" taps.
"""
import platform
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from .config import DUBAI_TZ, LOG_DIR, ROOT, RUN_HOUR_DUBAI, ensure_dirs, load_config
from .runner import watch_window

TASK_NAME = "CarWatchDaily"
WATCH_TASK_NAME = "CarWatchPreownedWatch"
SYNC_TASK_NAME = "CarWatchTelegramSync"
LAUNCHD_LABEL = "com.carwatch.daily"
WATCH_LAUNCHD_LABEL = "com.carwatch.watch"
SYNC_LAUNCHD_LABEL = "com.carwatch.telegram-sync"
CRON_MARKERS = ("carwatch run --scheduled", "carwatch watch --scheduled", "carwatch telegram-sync")


def local_hour_time(dubai_hour: int):
    """Today's dubai_hour:00 (Dubai time) expressed in this computer's local time zone."""
    return datetime.now(DUBAI_TZ).replace(hour=dubai_hour, minute=0, second=0, microsecond=0).astimezone()


def local_minute_time(dubai_minutes: int):
    """Today's time `dubai_minutes` after midnight Dubai, in this computer's local time zone."""
    return local_hour_time(0) + timedelta(minutes=dubai_minutes)


def local_run_time():
    """13:00 Dubai expressed in this computer's local time zone."""
    return local_hour_time(RUN_HOUR_DUBAI)


def windows_xml(description: str, trigger: str, args: str, time_limit: str) -> str:
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = pythonw if pythonw.exists() else Path(sys.executable)
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>{description}</Description></RegistrationInfo>
  <Triggers>{trigger}</Triggers>
  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>{time_limit}</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{exe}</Command>
      <Arguments>{args}</Arguments>
      <WorkingDirectory>{ROOT}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def windows_tasks():
    start = local_run_time().strftime("%Y-%m-%dT%H:%M:%S")
    daily = windows_xml(
        "Car Watch: daily used-hybrid listing check (1pm Dubai)",
        f"<CalendarTrigger><StartBoundary>{start}</StartBoundary><Enabled>true</Enabled>"
        "<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger>",
        "-m carwatch run --scheduled", "PT1H")
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    sync = windows_xml(
        "Car Watch: apply Telegram 'Not interested' taps (every 15 minutes)",
        f"<TimeTrigger><StartBoundary>{now}</StartBoundary><Enabled>true</Enabled>"
        "<Repetition><Interval>PT15M</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition>"
        "</TimeTrigger>",
        "-m carwatch telegram-sync", "PT5M")
    cfg = load_config()
    start, end = watch_window(cfg)
    every = cfg["watch"].get("interval_minutes", 60)
    first = local_minute_time(start).strftime("%Y-%m-%dT%H:%M:%S")
    w = cfg["watch"]
    watch = windows_xml(
        f"Car Watch: Toyota/Honda pre-owned watch every {every} min ({w['start']}-{w['end']} Dubai)",
        f"<CalendarTrigger><StartBoundary>{first}</StartBoundary><Enabled>true</Enabled>"
        f"<Repetition><Interval>PT{every}M</Interval><Duration>PT{end - start + 5}M</Duration>"
        "<StopAtDurationEnd>false</StopAtDurationEnd></Repetition>"
        "<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger>",
        "-m carwatch watch --scheduled", "PT20M")
    return {TASK_NAME: daily, WATCH_TASK_NAME: watch, SYNC_TASK_NAME: sync}


def launchd_plist(label: str, args: list, timing: str) -> str:
    argv = "".join(f"<string>{a}</string>" for a in [sys.executable, "-m", "carwatch", *args])
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key><array>{argv}</array>
  <key>WorkingDirectory</key><string>{ROOT}</string>
  {timing}
  <key>StandardOutPath</key><string>{LOG_DIR / 'scheduler.log'}</string>
  <key>StandardErrorPath</key><string>{LOG_DIR / 'scheduler.log'}</string>
</dict></plist>
"""


def launchd_agents():
    t = local_run_time()
    # launchd runs a missed calendar job when the Mac wakes from sleep
    daily = launchd_plist(LAUNCHD_LABEL, ["run", "--scheduled"],
                          f"<key>StartCalendarInterval</key><dict><key>Hour</key><integer>{t.hour}</integer>"
                          f"<key>Minute</key><integer>{t.minute}</integer></dict>")
    sync = launchd_plist(SYNC_LAUNCHD_LABEL, ["telegram-sync"], "<key>StartInterval</key><integer>900</integer>")
    cfg = load_config()
    start, end = watch_window(cfg)
    times = [local_minute_time(m) for m in range(start, end + 1, cfg["watch"].get("interval_minutes", 60))]
    slots = "".join(f"<dict><key>Hour</key><integer>{t.hour}</integer>"
                    f"<key>Minute</key><integer>{t.minute}</integer></dict>" for t in times)
    watch = launchd_plist(WATCH_LAUNCHD_LABEL, ["watch", "--scheduled"],
                          f"<key>StartCalendarInterval</key><array>{slots}</array>")
    return {LAUNCHD_LABEL: daily, WATCH_LAUNCHD_LABEL: watch, SYNC_LAUNCHD_LABEL: sync}


def _crontab_without_ours() -> list:
    current = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    return [l for l in current.splitlines() if not any(m in l for m in CRON_MARKERS)]


def install():
    ensure_dirs()
    system = platform.system()
    t = local_run_time()
    if system == "Windows":
        for name, xml in windows_tasks().items():
            with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False, encoding="utf-16") as f:
                f.write(xml)
            subprocess.run(["schtasks", "/Create", "/TN", name, "/XML", f.name, "/F"], check=True)
            Path(f.name).unlink()
        print(f"Task Scheduler: '{TASK_NAME}' runs daily at {t:%H:%M} local (= 13:00 Dubai); "
              "if the PC was asleep or off, it runs as soon as possible afterwards.\n"
              f"Task Scheduler: '{SYNC_TASK_NAME}' checks Telegram for 'Not interested' taps every 15 minutes.")
    elif system == "Darwin":
        for label, plist_text in launchd_agents().items():
            plist = Path.home() / "Library/LaunchAgents" / f"{label}.plist"
            plist.parent.mkdir(parents=True, exist_ok=True)
            plist.write_text(plist_text)
            subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
            subprocess.run(["launchctl", "load", str(plist)], check=True)
        print(f"launchd: daily run at {t:%H:%M} local (= 13:00 Dubai, catches up on wake); "
              "Telegram sync every 15 minutes.")
    else:
        # cron can't catch up after sleep, so check hourly; --scheduled runs at most once per daily window.
        log = LOG_DIR / "scheduler.log"
        # watch: every interval; `watch --scheduled` itself skips times outside 6am-11pm Dubai
        every = load_config().get("watch", {}).get("interval_minutes", 60)
        lines = [f'0 * * * * cd "{ROOT}" && "{sys.executable}" -m carwatch run --scheduled >> "{log}" 2>&1',
                 f'*/{every} * * * * cd "{ROOT}" && "{sys.executable}" -m carwatch watch --scheduled >> "{log}" 2>&1',
                 f'*/15 * * * * cd "{ROOT}" && "{sys.executable}" -m carwatch telegram-sync >> "{log}" 2>&1']
        subprocess.run(["crontab", "-"], input="\n".join(_crontab_without_ours() + lines) + "\n",
                       text=True, check=True)
        print("cron: hourly check (runs once per day after 13:00 Dubai, catches up after sleep); "
              "Telegram sync every 15 minutes.")


def uninstall():
    system = platform.system()
    if system == "Windows":
        for name in (TASK_NAME, WATCH_TASK_NAME, SYNC_TASK_NAME):
            subprocess.run(["schtasks", "/Delete", "/TN", name, "/F"], check=False)
    elif system == "Darwin":
        for label in (LAUNCHD_LABEL, WATCH_LAUNCHD_LABEL, SYNC_LAUNCHD_LABEL):
            plist = Path.home() / "Library/LaunchAgents" / f"{label}.plist"
            subprocess.run(["launchctl", "unload", str(plist)], check=False)
            plist.unlink(missing_ok=True)
    else:
        subprocess.run(["crontab", "-"], input="\n".join(_crontab_without_ours()) + "\n", text=True, check=True)
    print("Schedule removed.")
