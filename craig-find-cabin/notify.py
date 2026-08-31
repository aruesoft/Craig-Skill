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
