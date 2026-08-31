import requests


class TelegramError(Exception):
    pass


class Telegram:
    def __init__(self, token: str, chat_id: str | None):
        self.base = f"https://api.telegram.org/bot{token}"
        self.chat_id = chat_id

    def set_chat_id(self, chat_id: str):
        self.chat_id = str(chat_id)

    def send(self, text: str) -> None:
        if not self.chat_id:
            return
        try:
            requests.post(f"{self.base}/sendMessage",
                          data={"chat_id": self.chat_id, "text": text,
                                "disable_web_page_preview": "true"},
                          timeout=20)
        except requests.RequestException as e:
            print(f"[telegram] send failed: {e}")

    def send_photo(self, png: bytes, caption: str) -> None:
        if not self.chat_id:
            return
        try:
            requests.post(f"{self.base}/sendPhoto",
                          data={"chat_id": self.chat_id, "caption": caption},
                          files={"photo": ("captcha.png", png, "image/png")},
                          timeout=20)
        except requests.RequestException as e:
            print(f"[telegram] send_photo failed: {e}")

    def get_updates(self, offset=None, timeout: int = 30) -> list:
        params = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        try:
            r = requests.get(f"{self.base}/getUpdates", params=params,
                             timeout=timeout + 10)
            return r.json().get("result", [])
        except (requests.RequestException, ValueError) as e:
            print(f"[telegram] get_updates failed: {e}")
            return []


class Pushover:
    """Pushover 긴급(priority 2) 푸시 — 사용자가 앱에서 확인할 때까지 방해금지 무시하고 반복.
    user_key/app_token 둘 다 있어야 활성화."""

    def __init__(self, user: str | None, token: str | None):
        self.user = user
        self.token = token
        self.enabled = bool(user and token)

    def emergency(self, title: str, message: str) -> str | None:
        """긴급 알림 발송. 취소에 쓸 receipt를 반환(실패/비활성 시 None)."""
        if not self.enabled:
            return None
        try:
            r = requests.post("https://api.pushover.net/1/messages.json", data={
                "token": self.token, "user": self.user,
                "title": title, "message": message,
                "priority": 2, "retry": 30, "expire": 3600, "sound": "persistent",
            }, timeout=20)
            return r.json().get("receipt")
        except (requests.RequestException, ValueError) as e:
            print(f"[pushover] emergency failed: {e}")
            return None

    def cancel(self, receipt: str | None) -> None:
        """해결된 긴급 알림의 반복을 중단."""
        if not self.enabled or not receipt:
            return
        try:
            requests.post(f"https://api.pushover.net/1/receipts/{receipt}/cancel.json",
                          data={"token": self.token}, timeout=20)
        except requests.RequestException as e:
            print(f"[pushover] cancel failed: {e}")


class Caller:
    """Twilio 음성 전화 — 자리가 나면 폰이 실제로 울리게 한다. TTS(한국어)로 안내.
    sid/token/from/to 모두 있어야 활성화."""

    def __init__(self, sid: str | None, token: str | None,
                 from_number: str | None, to_number: str | None):
        self.sid = sid
        self.token = token
        self.from_number = from_number
        self.to_number = to_number
        self.enabled = bool(sid and token and from_number and to_number)

    def call(self, message: str) -> None:
        if not self.enabled:
            return
        say = message.replace("<", " ").replace("&", " ")
        twiml = (f'<Response><Say language="ko-KR">{say}</Say>'
                 f'<Pause length="1"/><Say language="ko-KR">{say}</Say></Response>')
        try:
            requests.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{self.sid}/Calls.json",
                data={"To": self.to_number, "From": self.from_number, "Twiml": twiml},
                auth=(self.sid, self.token), timeout=20)
        except requests.RequestException as e:
            print(f"[twilio] call failed: {e}")
