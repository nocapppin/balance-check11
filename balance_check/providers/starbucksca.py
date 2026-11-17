import asyncio
import os

import pyppeteer
import requests

from balance_check import config, logger
from balance_check.provider import BalanceCheckProvider
from balance_check.validators.gift_card import GiftCardSchema, Merchant

INVALID_CARD_CODES = {"121017"}
# Starbucks edge often 429s ancient Firefox UAs used elsewhere in this project.
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


class StarbucksCA(BalanceCheckProvider):
    """Unauthenticated Starbucks Canada gift-card balance check (no CAPTCHA)."""

    def __init__(self):
        super().__init__()

        self.website_url = "https://www.starbucks.ca/gift"
        self.api_path = "/apiproxy/v1/gift/card/check-balance"
        self.api_endpoint = "https://www.starbucks.ca" + self.api_path
        self.schema = GiftCardSchema(Merchant.StarbucksCA)
        self.market = "CA"
        self.max_workers = 1
        self.user_agent = config.USER_AGENT if "Chrome/" in config.USER_AGENT else BROWSER_UA

    def _parse_payload(self, status_code, payload, raw_text=""):
        if status_code == 200:
            balance = None
            if isinstance(payload, dict):
                balance = payload.get("balance")
                if balance is None and isinstance(payload.get("data"), dict):
                    balance = payload["data"].get("balance")

            if balance is None:
                raise RuntimeError(
                    "Balance check succeeded but no balance field was returned"
                )

            if isinstance(balance, dict):
                amount = balance.get("amount", balance)
            else:
                amount = balance

            logger.info(f"Success! Card balance: {amount}")
            return {"balance": amount}

        error = payload.get("error") if isinstance(payload, dict) else None
        code = None
        message = raw_text or "Unknown error"
        if isinstance(error, dict):
            code = str(error.get("code") or "")
            message = error.get("message") or message

        if status_code == 400 or code in INVALID_CARD_CODES:
            raise RuntimeError(
                "Invalid Starbucks card number or security code"
                + (f" (code {code})" if code else "")
            )

        if status_code == 429:
            raise RuntimeError(
                "Starbucks rate-limited the balance request; try again later"
            )

        raise RuntimeError(
            f"Failed Starbucks CA balance check "
            f"(status {status_code}): {message}"
        )

    def _check_via_requests(self, card_number, pin):
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": self.user_agent,
                "Accept-Language": "en-CA,en;q=0.9",
            }
        )

        logger.info("Fetching Starbucks.ca gift page")
        page = session.get(self.website_url, timeout=30)
        if page.status_code != 200:
            raise RuntimeError(
                f"Failed to load Starbucks.ca gift page "
                f"(status code {page.status_code})"
            )

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://www.starbucks.ca",
            "Referer": self.website_url,
        }
        body = {
            "cardNumber": card_number,
            "pin": pin,
            "market": self.market,
        }

        logger.info("Fetching balance from Starbucks.ca API")
        resp = session.post(
            self.api_endpoint, json=body, headers=headers, timeout=30
        )

        raw = resp.text or ""
        payload = None
        if raw:
            try:
                payload = resp.json()
            except ValueError:
                payload = None

        # Empty 429/WAF bodies are common from some IPs; caller may fall back.
        if resp.status_code == 429 or (
            resp.status_code >= 400 and not payload and not raw
        ):
            raise RuntimeError(
                f"Starbucks blocked direct HTTP access (status {resp.status_code})"
            )

        return self._parse_payload(resp.status_code, payload or {}, raw)

    async def _check_via_browser_async(self, card_number, pin):
        logger.info("Falling back to headless browser for Starbucks.ca API")
        launch_kwargs = {
            "handleSIGINT": False,
            "handleSIGTERM": False,
            "handleSIGHUP": False,
            "args": [
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--headless=new",
            ],
        }
        # Prefer system Chrome when present; pyppeteer's bundled Chromium is often blocked.
        for chrome_path in (
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium-browser",
            "/usr/bin/chromium",
        ):
            if os.path.exists(chrome_path):
                launch_kwargs["executablePath"] = chrome_path
                break

        browser = await pyppeteer.launch(**launch_kwargs)
        try:
            page = await browser.newPage()
            await page.setUserAgent(self.user_agent)
            await page.goto(self.website_url, {"waitUntil": "domcontentloaded"})

            result = await page.evaluate(
                """(cardNumber, pin, market, apiPath) => {
                    return fetch(apiPath, {
                      method: 'POST',
                      credentials: 'same-origin',
                      headers: {
                        'accept': 'application/json',
                        'content-type': 'application/json',
                        'X-Requested-With': 'XMLHttpRequest'
                      },
                      body: JSON.stringify({cardNumber, pin, market})
                    }).then(async (response) => {
                      const text = await response.text();
                      let body = null;
                      try { body = JSON.parse(text); } catch (e) {}
                      return {status: response.status, body, text};
                    });
                }""",
                card_number,
                pin,
                self.market,
                self.api_path,
            )
        finally:
            await browser.close()

        if not result:
            raise RuntimeError("No response from Starbucks.ca browser balance check")

        return self._parse_payload(
            result.get("status"), result.get("body") or {}, result.get("text") or ""
        )

    def _check_via_browser(self, card_number, pin):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(
                self._check_via_browser_async(card_number, pin)
            )
        finally:
            loop.close()

    def scrape(self, **kwargs):
        card_number = kwargs["card_number"]
        pin = kwargs["pin"]

        try:
            return self._check_via_requests(card_number, pin)
        except (requests.exceptions.RequestException, RuntimeError) as err:
            message = str(err).lower()
            retryable = any(
                token in message
                for token in (
                    "429",
                    "blocked direct http",
                    "rate-limited",
                    "timed out",
                    "timeout",
                    "connection",
                )
            )
            if not retryable:
                raise
            logger.warning(
                f"Direct Starbucks.ca request failed ({err}); using browser"
            )
            return self._check_via_browser(card_number, pin)

    def check_balance(self, **kwargs):
        if self.validate(kwargs):
            logger.info(f"Checking balance for card: {kwargs['card_number']}")
            return self.scrape(
                card_number=kwargs["card_number"], pin=kwargs["pin"]
            )
