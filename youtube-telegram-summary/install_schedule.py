#!/usr/bin/env python3
"""
매시간 자동 실행 등록 (크로스플랫폼)

  - macOS / Linux : crontab
  - Windows       : 작업 스케줄러(schtasks), pythonw.exe 로 콘솔창 없이 실행

사용법:
  python install_schedule.py                # 매시간 등록
  python install_schedule.py --interval 6   # 6시간마다
  python install_schedule.py --status       # 등록 상태 확인
  python install_schedule.py --remove       # 등록 해제
"""

import argparse
import json
import platform
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
MONITOR = SCRIPT_DIR / "monitor.py"
LOG_FILE = SCRIPT_DIR / "monitor.log"
TASK_NAME = "YouTubeTelegramSummary"

CONFIG_FILE = Path.home() / ".config" / "youtube-telegram-summary" / "config.json"

IS_WINDOWS = platform.system() == "Windows"


def _save_interval_to_config(interval_hours):
    """미실행 감지가 주기를 알 수 있도록 config에 기록"""
    try:
        if not CONFIG_FILE.exists():
            return
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        cfg["schedule_interval_hours"] = interval_hours
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"   (config에 실행 주기 {interval_hours}시간 기록)")
    except Exception:
        pass


# ──────────────────────── Windows (schtasks) ────────────────────────

def _pythonw_path():
    """콘솔창이 뜨지 않는 pythonw.exe 경로 (없으면 python.exe)"""
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        cand = exe[:-len("python.exe")] + "pythonw.exe"
        if Path(cand).exists():
            return cand
    return exe


def install_windows(interval_hours):
    pythonw = _pythonw_path()
    # pythonw 는 표준출력이 없으므로 --logfile 로 로그를 파일에 남긴다
    tr = f'"{pythonw}" "{MONITOR}" --logfile "{LOG_FILE}"'
    cmd = ["schtasks", "/Create", "/SC", "HOURLY", "/MO", str(interval_hours),
           "/TN", TASK_NAME, "/TR", tr, "/F"]
    print("등록 명령:\n  " + " ".join(cmd) + "\n")
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"⚠️ 작업 등록 실패: {e}")
        print("   관리자 권한 PowerShell/명령 프롬프트에서 다시 시도해 보세요.")
        return
    _save_interval_to_config(interval_hours)
    print(f"\n✅ Windows 작업 스케줄러 등록 완료: '{TASK_NAME}' ({interval_hours}시간마다)")
    print(f"   로그 파일: {LOG_FILE}")
    print(f"   상태 확인: schtasks /Query /TN {TASK_NAME}")
    print(f"   즉시 실행: schtasks /Run /TN {TASK_NAME}")
    print(f"   등록 해제: python install_schedule.py --remove")
    print("\n참고: 기본적으로 '로그인했을 때만' 실행됩니다. PC가 켜져 있고 로그인된 상태에서 동작합니다.")


def remove_windows():
    cmd = ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"]
    try:
        subprocess.run(cmd, check=True)
        print(f"✅ 작업 '{TASK_NAME}' 삭제 완료")
    except subprocess.CalledProcessError:
        print(f"등록된 작업 '{TASK_NAME}' 이(가) 없습니다.")


def status_windows():
    subprocess.run(["schtasks", "/Query", "/TN", TASK_NAME, "/V", "/FO", "LIST"])


# ──────────────────────── macOS / Linux (crontab) ────────────────────────

def _cron_line(interval_hours):
    sched = "0 * * * *" if interval_hours == 1 else f"0 */{interval_hours} * * *"
    return f"{sched} {sys.executable} {MONITOR} >> {LOG_FILE} 2>&1"


def install_unix(interval_hours):
    cron_line = _cron_line(interval_hours)
    print(f"등록할 crontab 줄:\n  {cron_line}\n")
    try:
        res = subprocess.run(['crontab', '-l'], capture_output=True, text=True, timeout=10)
        existing = res.stdout if res.returncode == 0 else ""
        if str(MONITOR) in existing:
            print("이미 crontab에 등록되어 있습니다. (기존 항목 유지)")
            return
        new_cron = (existing.rstrip('\n') + "\n" + cron_line + "\n").lstrip('\n')
        subprocess.run(['crontab', '-'], input=new_cron, text=True, check=True, timeout=10)
        _save_interval_to_config(interval_hours)
        print(f"✅ crontab 등록 완료 ({interval_hours}시간마다). 확인: crontab -l")
        print(f"   로그 파일: {LOG_FILE}")
    except subprocess.TimeoutExpired:
        print("⚠️ crontab 등록이 멈췄습니다 (macOS 권한 문제 가능).")
        print("   시스템 설정 → 개인정보 보호 및 보안 → 전체 디스크 접근 권한 에 터미널 추가 후")
        print(f"   직접 등록하세요:  crontab -e  →  {cron_line}")
    except Exception as e:
        print(f"⚠️ crontab 자동 등록 실패: {e}")
        print(f"   직접 추가:  crontab -e  →  {cron_line}")


def remove_unix():
    try:
        res = subprocess.run(['crontab', '-l'], capture_output=True, text=True, timeout=10)
        if res.returncode != 0:
            print("등록된 crontab이 없습니다.")
            return
        lines = [ln for ln in res.stdout.splitlines() if str(MONITOR) not in ln]
        subprocess.run(['crontab', '-'], input="\n".join(lines) + "\n", text=True, check=True, timeout=10)
        print("✅ crontab 등록 해제 완료")
    except Exception as e:
        print(f"해제 실패: {e}")


def status_unix():
    res = subprocess.run(['crontab', '-l'], capture_output=True, text=True)
    lines = [ln for ln in res.stdout.splitlines() if str(MONITOR) in ln]
    if lines:
        print("등록된 cron 작업:")
        for ln in lines:
            print("  " + ln)
    else:
        print("등록된 cron 작업이 없습니다.")


# ──────────────────────────── 진입점 ────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="매시간 자동 실행 등록 (cron / Windows 작업 스케줄러)")
    ap.add_argument('--interval', type=int, default=1, help='실행 주기(시간). 기본 1')
    ap.add_argument('--remove', action='store_true', help='등록 해제')
    ap.add_argument('--status', action='store_true', help='등록 상태 확인')
    args = ap.parse_args()

    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

    if args.status:
        (status_windows if IS_WINDOWS else status_unix)()
    elif args.remove:
        (remove_windows if IS_WINDOWS else remove_unix)()
    else:
        if IS_WINDOWS:
            install_windows(args.interval)
        else:
            install_unix(args.interval)


if __name__ == '__main__':
    main()
