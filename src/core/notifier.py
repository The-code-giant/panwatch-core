import logging
import os
import re

import apprise
import asyncio
import httpx

logger = logging.getLogger(__name__)


def get_global_proxy() -> str:
    """Get the global HTTP proxy setting"""
    try:
        from src.web.database import SessionLocal
        from src.web.models import AppSettings

        db = SessionLocal()
        try:
            setting = (
                db.query(AppSettings).filter(AppSettings.key == "http_proxy").first()
            )
            return setting.value if setting and setting.value else ""
        finally:
            db.close()
    except Exception:
        return ""


def sanitize_for_telegram(content: str) -> str:
    """Sanitize content for Telegram (strip HTML and Markdown formatting)"""
    # Strip HTML tags
    content = re.sub(r"</?table[^>]*>", "", content)
    content = re.sub(r"</?thead[^>]*>", "", content)
    content = re.sub(r"</?tbody[^>]*>", "", content)
    content = re.sub(r"</?tr[^>]*>", "\n", content)
    content = re.sub(r"</?th[^>]*>", " | ", content)
    content = re.sub(r"</?td[^>]*>", " | ", content)
    content = re.sub(r"</?div[^>]*>", "", content)
    content = re.sub(r"</?span[^>]*>", "", content)
    content = re.sub(r"</?p[^>]*>", "\n", content)
    content = re.sub(r"<br\s*/?>", "\n", content)

    # Strip Markdown formatting
    # Markdown link [label](url) -> "label url": for Telegram inline links against
    # localhost/IP:port etc. non-public addresses aren't rendered (the tag degrades to
    # unclickable plain text), whereas a bare URL is auto-detected as clickable, which is more robust.
    content = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r"\1 \2", content)
    content = re.sub(r"^#{1,6}\s*", "", content, flags=re.MULTILINE)  # strip headings #
    content = re.sub(r"\*\*(.+?)\*\*", r"\1", content)  # strip bold **
    content = re.sub(r"\*(.+?)\*", r"\1", content)  # strip italic *
    content = re.sub(r"__(.+?)__", r"\1", content)  # strip bold __
    content = re.sub(r"_(.+?)_", r"\1", content)  # strip italic _
    content = re.sub(r"~~(.+?)~~", r"\1", content)  # strip strikethrough
    content = re.sub(r"`(.+?)`", r"\1", content)  # strip inline code
    content = re.sub(
        r"^\s*[-*+]\s+", "· ", content, flags=re.MULTILINE
    )  # convert list markers to ·
    content = re.sub(
        r"^\s*\d+\.\s+", "", content, flags=re.MULTILINE
    )  # strip ordered list numbers

    # Clean up extra whitespace
    content = re.sub(r"\n\s*\n\s*\n", "\n\n", content)
    content = re.sub(r" +", " ", content)
    return content.strip()


# Channel type definitions (label + form fields)
CHANNEL_TYPES = {
    "telegram": {
        "label": "Telegram",
        "fields": ["bot_token", "chat_id", "proxy"],
    },
    "bark": {
        "label": "Bark",
        "fields": ["device_key", "server_url"],
    },
    "dingtalk": {
        "label": "DingTalk Bot",
        "fields": [
            "token",
            "secret",
            "phones",
            "keyword",
        ],  # keyword is optional: auto-appended when the bot's security setting is "keyword"
    },
    "wecom": {
        "label": "WeCom Bot",
        "fields": ["webhook_key"],
    },
    "lark": {
        "label": "Feishu (Lark) Bot",
        "fields": ["webhook_token"],
    },
    "serverchan": {
        "label": "ServerChan",
        "fields": ["sendkey"],
    },
    "pushplus": {
        "label": "PushPlus",
        "fields": ["token", "topic"],
    },
    "discord": {
        "label": "Discord",
        "fields": ["webhook_id", "webhook_token"],
    },
    "pushover": {
        "label": "Pushover",
        "fields": ["user_key", "app_token"],
    },
}

# Channel types supported via Apprise (when no proxy is configured)
_APPRISE_TYPES = {"telegram", "bark", "dingtalk", "lark", "discord", "pushover"}

# Channel types with a custom implementation (proxy support or special requirements)
_CUSTOM_IMPL_TYPES = {"wecom", "serverchan", "pushplus"}

# Channels that support Markdown (don't need sanitizing)
_MARKDOWN_CHANNELS = {"wecom", "serverchan", "pushplus", "dingtalk", "lark", "discord"}

# Channels that don't support Markdown (need sanitizing)
_PLAIN_TEXT_CHANNELS = {"telegram", "bark", "pushover"}


def build_apprise_url(channel_type: str, config: dict) -> str | None:
    """
    Build an Apprise URL from the channel type and config

    Returns:
        The Apprise URL, or None if a custom send path is needed (e.g. Telegram with a proxy)
    """
    if channel_type == "telegram":
        bot_token = config.get("bot_token", "")
        chat_id = config.get("chat_id", "")
        if not bot_token or not chat_id:
            raise ValueError("Telegram requires bot_token and chat_id")
        # If a proxy is configured (channel-level or global), return None to use the custom send path
        proxy = config.get("proxy", "").strip() or get_global_proxy()
        if proxy:
            return None
        return f"tgram://{bot_token}/{chat_id}"

    elif channel_type == "bark":
        device_key = config.get("device_key", "")
        server_url = config.get("server_url", "").strip("/")
        if not device_key:
            raise ValueError("Bark requires device_key")
        if server_url:
            host = server_url.replace("https://", "").replace("http://", "")
            return f"bark://{host}/{device_key}/"
        return f"bark://{device_key}/"

    elif channel_type == "dingtalk":
        # Apprise DingTalk format:
        # - No signing:  dingtalk://{access_token}/
        # - With signing: dingtalk://{secret}@{access_token}/
        # - @ phone numbers: append ?to=13800138000,13900139000 to the end of the URL
        token = (config.get("token") or "").strip()
        secret = (config.get("secret") or "").strip()
        phones = (config.get("phones") or "").strip()
        if not token:
            raise ValueError("DingTalk requires a token")
        base = f"dingtalk://{secret}@{token}/" if secret else f"dingtalk://{token}/"
        if phones:
            # Keep only digits and commas
            phone_list = [
                re.sub(r"[^0-9]", "", p)
                for p in phones.split(",")
                if re.sub(r"[^0-9]", "", p)
            ]
            if phone_list:
                base += f"?to={','.join(phone_list)}"
        return base

    elif channel_type == "lark":
        webhook_token = config.get("webhook_token", "")
        if not webhook_token:
            raise ValueError("Feishu (Lark) requires webhook_token")
        return f"lark://{webhook_token}/"

    elif channel_type == "discord":
        webhook_id = config.get("webhook_id", "")
        webhook_token = config.get("webhook_token", "")
        if not webhook_id or not webhook_token:
            raise ValueError("Discord requires webhook_id and webhook_token")
        return f"discord://{webhook_id}/{webhook_token}/"

    elif channel_type == "pushover":
        user_key = config.get("user_key", "")
        app_token = config.get("app_token", "")
        if not user_key or not app_token:
            raise ValueError("Pushover requires user_key and app_token")
        return f"pover://{user_key}@{app_token}/"

    else:
        raise ValueError(f"Unsupported Apprise channel type: {channel_type}")


class NotifierManager:
    """Notification manager: Apprise channels + custom channels"""

    def __init__(self, policy=None):
        self._ap = apprise.Apprise()
        self._custom_channels: list[tuple[str, dict]] = []
        self._channel_count = 0
        # DingTalk keyword (optional): auto-appended if the group bot has "keyword" security verification enabled
        self._dingtalk_keywords: set[str] = set()
        self.policy = policy

    def add_channel(self, channel_type: str, config: dict):
        """Add a notification channel"""
        try:
            if channel_type in _APPRISE_TYPES:
                url = build_apprise_url(channel_type, config)
                if url is None:
                    # Needs a custom implementation (e.g. Telegram with a proxy)
                    self._custom_channels.append((channel_type, config))
                    self._channel_count += 1
                    logger.info(f"Registered custom notification channel: {channel_type} (with proxy)")
                elif self._ap.add(url):
                    self._channel_count += 1
                    logger.info(f"Registered notification channel: {channel_type}")
                else:
                    logger.error(f"Failed to register notification channel: {channel_type} (invalid URL)")
                if channel_type == "dingtalk":
                    kw = (config.get("keyword") or "").strip()
                    if kw:
                        self._dingtalk_keywords.add(kw)
            else:
                self._custom_channels.append((channel_type, config))
                self._channel_count += 1
                logger.info(f"Registered custom notification channel: {channel_type}")
        except ValueError as e:
            logger.error(f"Failed to register notification channel: {e}")

    async def notify(self, title: str, content: str, images: list[str] | None = None):
        """Send a notification to all registered channels (errors are ignored)"""
        await self.notify_with_result(title, content, images)

    async def notify_with_result(
        self,
        title: str,
        content: str,
        images: list[str] | None = None,
        *,
        bypass_quiet_hours: bool = False,
    ) -> dict:
        """Send a notification to all registered channels, returning the result"""
        if self._channel_count == 0:
            logger.warning("No notification channel available")
            return {"success": False, "error": "No notification channel available"}

        # Quiet hours
        try:
            if not bypass_quiet_hours and getattr(self, "policy", None):
                if self.policy.is_quiet_now():
                    logger.info("Currently within quiet hours; skipping send")
                    return {"success": False, "skipped": "quiet_hours"}
        except Exception:
            # do not block sends on policy errors
            pass

        # Prepare a plain-text version (for channels that don't support Markdown)
        plain_content = sanitize_for_telegram(content)

        # Prepare attachments
        attachments = None
        if images:
            attachments = apprise.AppriseAttachment()
            for img_path in images:
                if img_path and os.path.exists(img_path):
                    attachments.add(img_path)

        errors = []

        # If DingTalk keywords are configured, auto-append them at the end of the content to pass "keyword" verification
        if self._dingtalk_keywords:
            suffix = " " + " ".join(sorted(self._dingtalk_keywords))
            if suffix.strip() not in plain_content:
                plain_content = (plain_content + "\n" + suffix).strip()
            if suffix.strip() not in content:
                content = (content + "\n" + suffix).strip()

        retry_attempts = 0
        backoff = 0.0
        try:
            if getattr(self, "policy", None):
                retry_attempts = max(0, int(self.policy.retry_attempts))
                backoff = float(self.policy.retry_backoff_seconds or 0.0)
        except Exception:
            retry_attempts = 0
            backoff = 0.0

        async def _sleep_retry(i: int):
            if backoff <= 0:
                return
            await asyncio.sleep(backoff * (2 ** max(0, i - 1)))

        # Apprise channels (uses plain text, since Telegram etc. don't support Markdown)
        if len(self._ap) > 0:
            apprise_ok = False
            last_err = ""
            for attempt in range(0, retry_attempts + 1):
                try:
                    success = await self._ap.async_notify(
                        title=title,
                        body=plain_content,
                        body_format=apprise.NotifyFormat.TEXT,
                        attach=attachments,
                    )
                    if success:
                        apprise_ok = True
                        logger.info(f"Apprise notification sent successfully: {title}")
                        break
                    last_err = "Apprise notification failed (possibly a network issue or misconfiguration)"
                    logger.error(f"{last_err}: {title}")
                except Exception as e:
                    last_err = f"Apprise notification error: {e}"
                    logger.error(last_err)
                if attempt < retry_attempts:
                    await _sleep_retry(attempt + 1)
            if not apprise_ok:
                errors.append(last_err or "Apprise notification failed")

        # Custom channels (format is chosen automatically based on channel type)
        for ch_type, config in self._custom_channels:
            ch_ok = False
            last_err = ""
            for attempt in range(0, retry_attempts + 1):
                try:
                    # Channels that support Markdown use the raw content, otherwise use plain text
                    ch_content = (
                        content if ch_type in _MARKDOWN_CHANNELS else plain_content
                    )
                    await self._send_custom(ch_type, config, title, ch_content)
                    ch_ok = True
                    break
                except Exception as e:
                    last_err = f"{ch_type} send failed: {e}"
                    logger.error(last_err)
                if attempt < retry_attempts:
                    await _sleep_retry(attempt + 1)
            if not ch_ok:
                errors.append(last_err or f"{ch_type} send failed")

        if errors:
            return {"success": False, "error": "; ".join(errors)}
        return {"success": True}

    async def _send_custom(self, ch_type: str, config: dict, title: str, content: str):
        """Send a notification through a custom channel"""
        if ch_type == "telegram":
            await self._send_telegram(config, title, content)
        elif ch_type == "wecom":
            await self._send_wecom(config, title, content)
        elif ch_type == "serverchan":
            await self._send_serverchan(config, title, content)
        elif ch_type == "pushplus":
            await self._send_pushplus(config, title, content)
        else:
            logger.warning(f"Unknown custom channel type: {ch_type}")

    async def _send_telegram(self, config: dict, title: str, content: str):
        """Telegram Bot API (proxy-capable)

        Telegram's legacy Markdown parser is fragile:
        - It doesn't recognize `**bold**` (only `*bold*`); GitHub-flavored Markdown causes
          "Can't find end of entity"
        - It doesn't recognize `### heading` (treats # as a plain character, and text after
          ### may get truncated)
        - There's a 4096-character limit per message; exceeding it truncates and breaks entities
        We do compatibility preprocessing + truncation before sending.
        """
        bot_token = config.get("bot_token", "")
        chat_id = config.get("chat_id", "")
        # Channel-level proxy takes priority, otherwise fall back to the global proxy
        proxy = config.get("proxy", "").strip() or get_global_proxy()

        if not bot_token or not chat_id:
            raise ValueError("Telegram requires bot_token and chat_id")

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        # Use sanitize_for_telegram to strip Markdown down to plain text entirely,
        # avoiding Telegram parse failures from `**bold**` / `## heading` / unclosed entities.
        # We manually wrap the title in `*...*` to bold it (Telegram's legacy Markdown only
        # recognizes single asterisks).
        safe_title = sanitize_for_telegram(title) if title else ""
        safe_content = sanitize_for_telegram(content)
        text = f"*{safe_title}*\n\n{safe_content}" if safe_title else safe_content
        # Telegram's per-message limit is 4096; leave some buffer for the trailing notice
        if len(text) > 3900:
            # If the body ends with a details link (already a bare URL after sanitizing),
            # a blunt truncation would cut it off, leaving the user unable to click through.
            # Extract it first, truncate the body, then reattach it at the end.
            link_m = re.search(r"(https?://[^\s)]+)\s*$", text)
            if link_m:
                notice = f"\n\n...content truncated, full report: {link_m.group(1)}"
            else:
                notice = "\n\n...content truncated, view the full report in TickerKeep"
            text = text[: 3900 - len(notice)].rstrip() + notice
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }

        # Configure proxy
        transport = None
        if proxy:
            transport = httpx.AsyncHTTPTransport(proxy=proxy)
            logger.debug(f"Telegram using proxy: {proxy}")

        try:
            async with httpx.AsyncClient(transport=transport, timeout=30) as client:
                resp = await client.post(url, json=payload)
                data = resp.json()
                if not data.get("ok"):
                    raise RuntimeError(f"Telegram API error: {data.get('description')}")
                logger.info(f"Telegram notification sent successfully: {title}")
        except httpx.ConnectError as e:
            if proxy:
                raise RuntimeError(f"Failed to connect to proxy ({proxy}): {e}")
            else:
                raise RuntimeError(f"Could not connect to the Telegram API (a proxy may be required): {e}")
        except httpx.TimeoutException:
            raise RuntimeError("Request timed out (network issue or misconfigured proxy)")
        except Exception as e:
            if (
                "ConnectError" in str(type(e).__name__)
                or "connection" in str(e).lower()
            ):
                if not proxy:
                    raise RuntimeError(f"Network connection failed; consider configuring a proxy: {e}")
            raise

    async def _send_wecom(self, config: dict, title: str, content: str):
        """WeCom (Enterprise WeChat) bot webhook"""
        key = config.get("webhook_key", "")
        if not key:
            raise ValueError("WeCom requires webhook_key")

        url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={key}"
        text = f"## {title}\n\n{content}" if title else content
        payload = {"msgtype": "markdown", "markdown": {"content": text}}

        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, timeout=30)
            data = resp.json()
            if data.get("errcode") != 0:
                raise RuntimeError(f"WeCom send failed: {data.get('errmsg')}")
            logger.info(f"WeCom notification sent successfully: {title}")

    async def _send_serverchan(self, config: dict, title: str, content: str):
        """ServerChan push"""
        sendkey = config.get("sendkey", "")
        if not sendkey:
            raise ValueError("ServerChan requires sendkey")

        url = f"https://sctapi.ftqq.com/{sendkey}.send"
        payload = {"title": title or "Notification", "desp": content}

        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, timeout=30)
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"ServerChan send failed: {data.get('message')}")
            logger.info(f"ServerChan notification sent successfully: {title}")

    async def _send_pushplus(self, config: dict, title: str, content: str):
        """PushPlus push"""
        token = config.get("token", "")
        if not token:
            raise ValueError("PushPlus requires a token")

        url = "https://www.pushplus.plus/send"
        payload = {
            "token": token,
            "title": title or "Notification",
            "content": content,
            "template": "markdown",
        }
        topic = config.get("topic", "")
        if topic:
            payload["topic"] = topic

        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, timeout=30)
            data = resp.json()
            if data.get("code") != 200:
                raise RuntimeError(f"PushPlus send failed: {data.get('msg')}")
            logger.info(f"PushPlus notification sent successfully: {title}")
