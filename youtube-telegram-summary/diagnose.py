#!/usr/bin/env python3
"""
secondb.ai 실제 동작 점검기 (1회용 진단 도구)

login.py 로 구글 로그인을 끝낸 뒤 실행하세요.
실제 영상 URL을 제출하면서 secondb.ai가 어떻게 요약을 가져오는지
(네트워크 API 호출 + 화면 구조)를 캡처해 파일로 저장합니다.

저장 위치: ~/.config/youtube-telegram-summary/inspect/
  - home.png / home.html      : 로그인 후 첫 화면
  - after.png / after.html    : 영상 제출 후 화면
  - elements.json             : 입력창/버튼/textarea 목록
  - network.json              : 요약 생성 중 발생한 API 호출들 (핵심)
"""

import json
import sys
import time
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "youtube-telegram-summary"
PROFILE_DIR = CONFIG_DIR / "browser_profile"
OUT_DIR = CONFIG_DIR / "inspect"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# 점검에 사용할 실제 영상 (원하면 다른 URL로 바꿔도 됨)
TEST_VIDEO = "https://www.youtube.com/watch?v=NxVQhtQmwiw"


def dump_elements(page):
    """페이지의 입력창/버튼/textarea 정보를 수집"""
    js = """
    () => {
      const grab = (sel) => Array.from(document.querySelectorAll(sel)).map(el => ({
        tag: el.tagName.toLowerCase(),
        type: el.getAttribute('type') || '',
        name: el.getAttribute('name') || '',
        id: el.id || '',
        placeholder: el.getAttribute('placeholder') || '',
        ariaLabel: el.getAttribute('aria-label') || '',
        className: (el.className || '').toString().slice(0, 120),
        text: (el.innerText || '').trim().slice(0, 60),
        visible: !!(el.offsetWidth || el.offsetHeight)
      }));
      return {
        inputs: grab('input'),
        textareas: grab('textarea'),
        buttons: grab('button'),
        links: grab('a[href]').slice(0, 40)
      };
    }
    """
    try:
        return page.evaluate(js)
    except Exception as e:
        return {"error": str(e)}


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright가 없습니다:  pip install playwright && playwright install chromium")
        sys.exit(1)

    if not PROFILE_DIR.exists():
        print("로그인 세션이 없습니다. 먼저 다음을 실행하세요:")
        print("  python ~/youtube-telegram-summary/login.py")
        sys.exit(1)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    network = []

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=False,           # 화면을 보면서 진단
            user_agent=UA,
            viewport={'width': 1280, 'height': 900},
            args=['--disable-blink-features=AutomationControlled'],
        )
        page = context.pages[0] if context.pages else context.new_page()

        # 네트워크 응답 캡처 (JSON / api 경로 위주)
        def on_response(resp):
            try:
                url = resp.url
                ct = resp.headers.get("content-type", "")
                if ("application/json" in ct or "/api" in url or "graphql" in url
                        or "summar" in url.lower()):
                    body = None
                    try:
                        body = resp.text()[:8000]
                    except Exception:
                        body = "(본문 읽기 실패)"
                    network.append({
                        "method": resp.request.method,
                        "url": url,
                        "status": resp.status,
                        "content_type": ct,
                        "request_post_data": (resp.request.post_data or "")[:2000],
                        "response_body": body,
                    })
            except Exception:
                pass

        page.on("response", on_response)

        # 1) 로그인 후 첫 화면
        print("secondb.ai 접속 중...")
        page.goto("https://secondb.ai/", wait_until="networkidle", timeout=60000)
        time.sleep(3)

        body_text = ""
        try:
            body_text = page.inner_text("body")
        except Exception:
            pass
        logged_out = any(m in body_text for m in
                         ["Sign in with Google", "Create Your Own Summaries",
                          "Sign in to summarize", "Continue with Google"])
        print(f"\n>>> 로그인 상태 판정: {'❌ 로그아웃(데모) 상태' if logged_out else '✅ 로그인됨'}")
        if logged_out:
            print("    아직 로그인이 안 되어 있습니다. login.py 를 먼저 끝내주세요.")
            print("    (그래도 화면 구조는 저장합니다.)")

        (OUT_DIR / "home.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(OUT_DIR / "home.png"), full_page=True)
        (OUT_DIR / "elements_home.json").write_text(
            json.dumps(dump_elements(page), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"첫 화면 저장 완료: {OUT_DIR}/home.png")

        # 2) 영상 URL 제출 시도
        print(f"\n테스트 영상 제출 시도: {TEST_VIDEO}")
        url_input = None
        for sel in ['input[type="url"]', 'input[placeholder*="youtube" i]',
                    'input[placeholder*="URL" i]', 'input[placeholder*="링크"]',
                    'textarea', 'input[type="text"]', 'input']:
            url_input = page.query_selector(sel)
            if url_input:
                print(f"  입력창 발견: {sel}")
                break

        if url_input:
            try:
                url_input.click()
                url_input.fill(TEST_VIDEO)
                time.sleep(0.5)
                submit = None
                for sel in ['button[type="submit"]', 'button:has-text("요약")',
                            'button:has-text("Summarize")', 'button:has-text("Summary")',
                            'button:has-text("분석")', 'button:has-text("Go")']:
                    submit = page.query_selector(sel)
                    if submit:
                        print(f"  제출 버튼 발견: {sel}")
                        break
                if submit:
                    submit.click()
                else:
                    print("  제출 버튼을 못 찾아 Enter로 시도")
                    page.keyboard.press("Enter")
            except Exception as e:
                print(f"  제출 중 오류: {e}")
        else:
            print("  입력창을 찾지 못했습니다. (화면 구조는 저장됨)")

        # 3) 요약 생성 대기하며 네트워크/화면 캡처
        print("\n요약 생성 대기 중 (최대 90초)... 브라우저 화면을 함께 지켜봐 주세요.")
        for i in range(18):
            time.sleep(5)
            print(f"  ...{(i+1)*5}초")

        (OUT_DIR / "after.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(OUT_DIR / "after.png"), full_page=True)
        (OUT_DIR / "elements_after.json").write_text(
            json.dumps(dump_elements(page), ensure_ascii=False, indent=2), encoding="utf-8")
        (OUT_DIR / "network.json").write_text(
            json.dumps(network, ensure_ascii=False, indent=2), encoding="utf-8")
        (OUT_DIR / "final_url.txt").write_text(page.url, encoding="utf-8")

        print(f"\n✅ 진단 캡처 완료. 저장 위치: {OUT_DIR}")
        print("  - after.png        : 제출 후 화면 (요약이 보이는지 확인)")
        print("  - network.json     : 요약 API 호출 내역")
        print("  - elements_*.json  : 입력창/버튼 구조")
        print(f"  - 최종 URL: {page.url}")
        print(f"  - 캡처된 네트워크 호출 수: {len(network)}")

        input("\n확인했으면 Enter로 브라우저를 닫습니다... ")
        context.close()


if __name__ == '__main__':
    main()
