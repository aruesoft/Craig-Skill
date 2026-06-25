#!/usr/bin/env python3
"""
YouTube → secondb.ai → Telegram 초기 설정 마법사
"""

import json
import platform
import subprocess
import sys
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "youtube-telegram-summary"
CONFIG_FILE = CONFIG_DIR / "config.json"
HOME_DIR = Path.home() / "youtube-telegram-summary"


def hr(title=""):
    if title:
        print(f"\n{'─'*50}\n  {title}\n{'─'*50}")
    else:
        print(f"{'─'*50}")


def ask(prompt, default=None):
    if default:
        v = input(f"{prompt} [{default}]: ").strip()
        return v if v else default
    return input(f"{prompt}: ").strip()


# 채널 해석 함수는 monitor.py 의 것을 재사용
sys.path.insert(0, str(Path(__file__).parent))
try:
    from monitor import resolve_channel_id, get_channel_name
except Exception:
    resolve_channel_id = None
    get_channel_name = None


def setup_telegram():
    hr("1단계: Telegram 봇 설정")
    print("""
Telegram 봇 만들기:
  1. Telegram 앱에서 @BotFather 검색
  2. /newbot 전송 → 봇 이름 → 봇 사용자명(@..._bot) 입력
  3. BotFather가 API Token 발급 (예: 1234567890:ABCdef...)
""")
    token = ask("Telegram Bot Token")
    if not token:
        print("Token이 없으면 설정을 완료할 수 없습니다.")
        sys.exit(1)

    print(f"""
Chat ID 확인:
  1. 만든 봇에게 아무 메시지 전송
  2. 브라우저에서 접속: https://api.telegram.org/bot{token}/getUpdates
  3. "chat":{{"id": 숫자}} 의 숫자가 Chat ID
""")
    chat_id = ask("Telegram Chat ID")
    if not chat_id:
        print("Chat ID가 없으면 설정을 완료할 수 없습니다.")
        sys.exit(1)
    return token, chat_id


def setup_youtube_channels():
    hr("2단계: YouTube 채널 설정 (여러 개 가능)")
    print("""
채널은 아래 형태 모두 입력 가능합니다 (자동으로 채널 ID로 변환):
  - @핸들          예: @channelname
  - 채널 URL       예: https://www.youtube.com/@channelname
  - UC로 시작하는 ID  예: UCxxxxxxxxxxxxxxxxxxxxxx

여러 채널은 쉼표로 구분하세요.  나중에 monitor.py --add-channel 로도 추가됩니다.
""")
    raw = ask("채널 (쉼표 구분)")
    inputs = [c.strip() for c in raw.split(',') if c.strip()]
    if not inputs:
        print("채널을 하나 이상 입력해야 합니다.")
        sys.exit(1)

    channels = []
    for item in inputs:
        if resolve_channel_id:
            cid = resolve_channel_id(item)
            if not cid:
                print(f"  ⚠️  채널 ID를 찾지 못함 (건너뜀): {item}")
                continue
            name = get_channel_name(cid) if get_channel_name else cid
            print(f"  ✓ {name}  ({cid})")
            channels.append(cid)
        else:
            channels.append(item)

    if not channels:
        print("유효한 채널이 없습니다. 다시 시도하세요.")
        sys.exit(1)
    return channels


def install_packages():
    hr("3단계: 패키지 설치")
    print("필요 패키지: playwright, requests")
    if ask("지금 설치할까요? (y/n)", "y").lower() == 'y':
        subprocess.run([sys.executable, "-m", "pip", "install", "playwright", "requests"], check=True)
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
        print("설치 완료!")
    else:
        print("나중에 직접 설치: pip install playwright requests && playwright install chromium")


def google_login():
    hr("4단계: secondb.ai 구글 로그인")
    print("""
secondb.ai 는 구글 로그인을 사용합니다.
자동 입력은 구글이 차단하므로, 브라우저에서 직접 한 번 로그인합니다.
(세션은 저장되어 이후 자동 재사용됩니다.)
""")
    if ask("지금 구글 로그인을 진행할까요? (y/n)", "y").lower() == 'y':
        login_path = Path(__file__).parent / "login.py"
        subprocess.run([sys.executable, str(login_path)])
    else:
        print("나중에 실행: python ~/youtube-telegram-summary/login.py")


def setup_schedule():
    """자동 실행 등록 (OS 자동 감지: cron 또는 Windows 작업 스케줄러)"""
    is_windows = platform.system() == "Windows"
    label = "Windows 작업 스케줄러" if is_windows else "crontab"
    hr(f"5단계: 자동 실행 설정 (매시간, {label})")

    if ask(f"{label}에 매시간 실행을 등록할까요? (y/n)", "y").lower() != 'y':
        print("나중에 등록:  python install_schedule.py")
        return

    # 스케줄 등록 로직은 install_schedule.py 에 일원화
    installer = Path(__file__).parent / "install_schedule.py"
    subprocess.run([sys.executable, str(installer)])


def test_telegram(token, chat_id):
    try:
        import requests as req
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = {'chat_id': chat_id, 'text': '✅ YouTube-Telegram 요약봇 설정 완료!'}
        result = req.post(url, json=data, timeout=10).json()
        if result.get('ok'):
            print("텔레그램 테스트 메시지 전송 성공!")
        else:
            print(f"텔레그램 오류: {result.get('description', result)}")
    except Exception as e:
        print(f"테스트 실패: {e}")


def main():
    print("🎬 YouTube → secondb.ai → Telegram 설정 마법사")
    hr()
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    existing_config = {}
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            existing_config = json.load(f)
        print(f"기존 설정 발견: {CONFIG_FILE}")
        if ask("기존 설정을 덮어쓸까요? (y/n)", "n").lower() != 'y':
            print("설정을 유지합니다. (구글 로그인/crontab만 다시 하려면 login.py 를 직접 실행하세요.)")
            return

    token, chat_id = setup_telegram()
    channels = setup_youtube_channels()

    config = {
        "youtube_channels": channels,
        "telegram_bot_token": token,
        "telegram_chat_id": chat_id,
        "summary_language": "kr",
        "max_videos_per_channel_per_run": 2,  # 채널당 한 번에 보낼 최신 영상 수
        "schedule_interval_hours": 1,         # 스케줄 주기(미실행 감지에 사용)
        "healthcheck_ping_url": "",           # (선택) healthchecks.io 등 외부 감시 핑 URL
    }
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    try:
        CONFIG_FILE.chmod(0o600)  # Windows에서는 제한적이지만 오류는 나지 않음
    except Exception:
        pass
    print(f"\n✅ config.json 저장됨: {CONFIG_FILE}")

    install_packages()
    google_login()

    setup_schedule()

    hr("테스트")
    if ask("텔레그램으로 테스트 메시지를 보낼까요? (y/n)", "y").lower() == 'y':
        test_telegram(token, chat_id)

    monitor_path = Path(__file__).parent / "monitor.py"
    if ask("\n지금 바로 첫 실행을 해볼까요? (y/n)", "y").lower() == 'y':
        subprocess.run([sys.executable, str(monitor_path)])

    log_path = Path(__file__).parent / "monitor.log"
    print("\n🎉 설정 완료!")
    print(f"  채널 추가:  python \"{monitor_path}\" --add-channel @핸들")
    print(f"  채널 목록:  python \"{monitor_path}\" --list-channels")
    print(f"  스케줄 변경: python \"{Path(__file__).parent / 'install_schedule.py'}\" --status")
    print(f"  로그 파일:  {log_path}")


if __name__ == '__main__':
    main()
