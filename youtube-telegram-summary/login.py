#!/usr/bin/env python3
"""
secondb.ai 구글 로그인 (최초 1회 / 세션 만료 시)

브라우저 창이 열리면 직접 '구글로 로그인'을 진행하세요.
로그인이 끝나면 이 터미널로 돌아와 Enter를 누르면 세션이 저장되고,
이후 monitor.py 는 이 세션을 자동으로 재사용합니다.

구글은 자동 입력 로그인을 봇으로 차단하므로, 로그인은 사람이 직접 해야 합니다.
세션은 한 번 저장되면 보통 수 주간 유지됩니다.
"""

import sys
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "youtube-telegram-summary"
PROFILE_DIR = CONFIG_DIR / "browser_profile"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright가 없습니다:  pip install playwright && playwright install chromium")
        sys.exit(1)

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print(" secondb.ai 구글 로그인")
    print("=" * 60)
    print("""
1. 잠시 후 브라우저 창이 열립니다.
2. secondb.ai 에서 '구글로 로그인 / Sign in with Google' 을 눌러
   직접 로그인하세요.
3. 로그인이 완료되어 secondb.ai 메인 화면이 보이면,
   이 터미널로 돌아와 Enter 키를 누르세요.
""")
    input("준비되면 Enter를 눌러 브라우저를 엽니다... ")

    with sync_playwright() as p:
        # headless=False (창 표시) + 영속 프로필 → 로그인 상태가 디스크에 저장됨
        context = p.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=False,
            user_agent=UA,
            viewport={'width': 1280, 'height': 900},
            args=['--disable-blink-features=AutomationControlled'],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://secondb.ai/", wait_until="domcontentloaded")

        print("\n브라우저에서 구글 로그인을 완료하세요.")
        input("로그인이 끝났으면 여기서 Enter를 누르세요... ")

        # 세션은 영속 프로필에 자동 저장됨. 브라우저만 닫으면 됨.
        context.close()

    print(f"\n✅ 로그인 세션이 저장되었습니다: {PROFILE_DIR}")
    print("이제 monitor.py 가 이 세션으로 자동 동작합니다.")
    print("테스트:  python ~/youtube-telegram-summary/monitor.py --debug")


if __name__ == '__main__':
    main()
