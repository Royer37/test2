"""
Milanuncios Scraper - Core scraping logic using Playwright
"""
import asyncio
import random
import logging
from typing import Optional, Callable
from playwright.async_api import async_playwright, Page, Browser, BrowserContext
from .parser import parse_listing_page, parse_search_results
from .utils import get_random_user_agent, build_page_url

logger = logging.getLogger(__name__)


class MilanunciosScraper:
    def __init__(
        self,
        delay_min: float = 3.0,
        delay_max: float = 7.0,
        max_retries: int = 3,
        headless: bool = True,
        on_progress: Optional[Callable] = None,
    ):
        self.delay_min = delay_min
        self.delay_max = delay_max
        self.max_retries = max_retries
        self.headless = headless
        self.on_progress = on_progress
        self._stop_requested = False
        self.results = []
        self.errors = []

    def stop(self):
        self._stop_requested = True

    def _emit(self, event: str, data: dict):
        if self.on_progress:
            self.on_progress({"event": event, **data})

    async def _random_delay(self):
        delay = random.uniform(self.delay_min, self.delay_max)
        await asyncio.sleep(delay)

    async def _setup_context(self, browser: Browser) -> BrowserContext:
        context = await browser.new_context(
            user_agent=get_random_user_agent(),
            viewport={"width": 1366, "height": 768},
            locale="es-ES",
            timezone_id="Europe/Madrid",
            extra_http_headers={
                "Accept-Language": "es-ES,es;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        return context

    async def _fetch_page_with_retry(self, page: Page, url: str) -> bool:
        for attempt in range(self.max_retries):
            try:
                response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                if response and response.status == 200:
                    await page.wait_for_timeout(random.randint(1000, 2000))
                    return True
                elif response and response.status == 429:
                    logger.warning(f"Rate limited on {url}, waiting longer...")
                    await asyncio.sleep(random.uniform(10, 20))
                else:
                    logger.warning(f"HTTP {response.status if response else 'N/A'} on {url} (attempt {attempt+1})")
            except Exception as e:
                logger.error(f"Error fetching {url} (attempt {attempt+1}): {e}")
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(random.uniform(3, 6))
        return False

    async def scrape(
        self,
        base_url: str,
        max_pages: int = 5,
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
        location_filter: Optional[str] = None,
    ) -> list[dict]:
        self.results = []
        self.errors = []
        self._stop_requested = False

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=self.headless)
            try:
                await self._run_scrape(
                    browser, base_url, max_pages, min_price, max_price, location_filter
                )
            finally:
                await browser.close()

        return self.results

    async def _run_scrape(self, browser, base_url, max_pages, min_price, max_price, location_filter):
        context = await self._setup_context(browser)

        listing_urls = []
        page_num = 1

        # --- Phase 1: Collect listing URLs from search pages ---
        while page_num <= max_pages and not self._stop_requested:
            page_url = build_page_url(base_url, page_num)
            self._emit("page_start", {"page": page_num, "url": page_url})

            page = await context.new_page()
            try:
                success = await self._fetch_page_with_retry(page, page_url)
                if not success:
                    self._emit("page_error", {"page": page_num, "url": page_url})
                    break

                # Accept cookies if present
                try:
                    cookie_btn = page.locator("button:has-text('Aceptar'), button:has-text('Accept')")
                    if await cookie_btn.count() > 0:
                        await cookie_btn.first.click()
                        await page.wait_for_timeout(500)
                except Exception:
                    pass

                urls = await parse_search_results(page)
                if not urls:
                    self._emit("page_done", {"page": page_num, "found": 0, "message": "No more listings"})
                    break

                listing_urls.extend(urls)
                self._emit("page_done", {"page": page_num, "found": len(urls)})
                logger.info(f"Page {page_num}: found {len(urls)} listings")
            finally:
                await page.close()

            page_num += 1
            if page_num <= max_pages and not self._stop_requested:
                await self._random_delay()

        # --- Phase 2: Visit each listing ---
        total = len(listing_urls)
        self._emit("listings_found", {"total": total})

        for idx, url in enumerate(listing_urls, 1):
            if self._stop_requested:
                break

            self._emit("listing_start", {"current": idx, "total": total, "url": url})
            page = await context.new_page()
            try:
                success = await self._fetch_page_with_retry(page, url)
                if not success:
                    self.errors.append({"url": url, "error": "Failed to load"})
                    self._emit("listing_error", {"current": idx, "url": url})
                    continue

                listing = await parse_listing_page(page, url)

                # Apply filters
                if min_price and listing.get("price_numeric") and listing["price_numeric"] < min_price:
                    continue
                if max_price and listing.get("price_numeric") and listing["price_numeric"] > max_price:
                    continue
                if location_filter and listing.get("location"):
                    if location_filter.lower() not in listing["location"].lower():
                        continue

                self.results.append(listing)
                self._emit("listing_done", {
                    "current": idx,
                    "total": total,
                    "title": listing.get("title", ""),
                    "phone": listing.get("phone", ""),
                    "price": listing.get("price", ""),
                })

            except Exception as e:
                logger.error(f"Error parsing listing {url}: {e}")
                self.errors.append({"url": url, "error": str(e)})
                self._emit("listing_error", {"current": idx, "url": url, "error": str(e)})
            finally:
                await page.close()

            if idx < total and not self._stop_requested:
                await self._random_delay()

        await context.close()
