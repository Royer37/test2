"""
Parser module - extracts data from Milanuncios pages
"""
import re
import logging
from playwright.async_api import Page

logger = logging.getLogger(__name__)


async def parse_search_results(page: Page) -> list[str]:
    """Extract all listing URLs from a search results page."""
    urls = []
    try:
        # Wait for listings container
        await page.wait_for_selector("article, [class*='item'], [class*='listing'], [class*='ad-']", timeout=10000)
    except Exception:
        logger.warning("No listing container found on search page")

    # Multiple selector strategies for listing links
    selectors = [
        "article a[href*='/anuncio/']",
        "a[href*='/anuncio/']",
        "[class*='item'] a[href*='.htm']",
        "[class*='card'] a[href]",
        "h2 a, h3 a",
    ]

    seen = set()
    for selector in selectors:
        try:
            elements = await page.query_selector_all(selector)
            for el in elements:
                href = await el.get_attribute("href")
                if href and href not in seen:
                    if href.startswith("/"):
                        href = f"https://www.milanuncios.com{href}"
                    if "milanuncios.com" in href and href not in seen:
                        seen.add(href)
                        urls.append(href)
        except Exception as e:
            logger.debug(f"Selector {selector} failed: {e}")

    # Deduplicate preserving order
    return list(dict.fromkeys(urls))


async def parse_listing_page(page: Page, url: str) -> dict:
    """Extract all fields from an individual listing page."""
    listing = {
        "url": url,
        "title": "",
        "price": "",
        "price_numeric": None,
        "seller": "",
        "phone": "",
        "location": "",
        "description": "",
        "date": "",
    }

    # --- Title ---
    for sel in ["h1", "[class*='title'] h1", "[class*='ad-title']", "[class*='listing-title']"]:
        try:
            el = await page.query_selector(sel)
            if el:
                text = (await el.inner_text()).strip()
                if text:
                    listing["title"] = text
                    break
        except Exception:
            pass

    # --- Price ---
    for sel in [
        "[class*='price']",
        "[class*='Price']",
        "span[class*='price']",
        "[data-testid*='price']",
        ".price",
    ]:
        try:
            el = await page.query_selector(sel)
            if el:
                text = (await el.inner_text()).strip()
                if text and "€" in text or re.search(r'\d+', text):
                    listing["price"] = text
                    listing["price_numeric"] = extract_numeric_price(text)
                    break
        except Exception:
            pass

    # --- Description ---
    for sel in [
        "[class*='description']",
        "[class*='desc']",
        "[class*='body']",
        "article p",
        "[class*='text']",
    ]:
        try:
            el = await page.query_selector(sel)
            if el:
                text = (await el.inner_text()).strip()
                if len(text) > 20:
                    listing["description"] = text[:2000]
                    break
        except Exception:
            pass

    # --- Location ---
    for sel in [
        "[class*='location']",
        "[class*='Location']",
        "[class*='place']",
        "[data-testid*='location']",
        "[class*='zona']",
        "[class*='province']",
    ]:
        try:
            el = await page.query_selector(sel)
            if el:
                text = (await el.inner_text()).strip()
                if text:
                    listing["location"] = text
                    break
        except Exception:
            pass

    # --- Seller name ---
    for sel in [
        "[class*='seller']",
        "[class*='advertiser']",
        "[class*='user']",
        "[class*='vendor']",
        "[class*='anunciante']",
    ]:
        try:
            el = await page.query_selector(sel)
            if el:
                text = (await el.inner_text()).strip()
                if text and len(text) < 100:
                    listing["seller"] = text
                    break
        except Exception:
            pass

    # --- Date ---
    for sel in ["time", "[class*='date']", "[class*='fecha']", "[class*='Date']", "[datetime]"]:
        try:
            el = await page.query_selector(sel)
            if el:
                dt = await el.get_attribute("datetime")
                text = dt or (await el.inner_text()).strip()
                if text:
                    listing["date"] = text
                    break
        except Exception:
            pass

    # --- Phone (requires clicking reveal button) ---
    listing["phone"] = await extract_phone(page)

    # Fallback: extract from page text via regex
    if not listing["phone"]:
        try:
            body_text = await page.inner_text("body")
            phones = re.findall(r'(?<!\d)([6-9]\d{8})(?!\d)', body_text)
            if phones:
                listing["phone"] = phones[0]
        except Exception:
            pass

    return listing


async def extract_phone(page: Page) -> str:
    """Try to reveal and extract phone number."""
    # Try clicking phone reveal buttons
    phone_button_selectors = [
        "button:has-text('Ver teléfono')",
        "button:has-text('Mostrar teléfono')",
        "button:has-text('ver número')",
        "[class*='phone'] button",
        "[class*='telefono']",
        "[class*='contact'] button",
        "a[href^='tel:']",
    ]

    for sel in phone_button_selectors:
        try:
            btn = await page.query_selector(sel)
            if btn:
                # Check for tel: link first
                href = await btn.get_attribute("href")
                if href and href.startswith("tel:"):
                    return href.replace("tel:", "").strip()

                await btn.click()
                await page.wait_for_timeout(1500)

                # Look for revealed phone
                for phone_sel in [
                    "a[href^='tel:']",
                    "[class*='phone']",
                    "[class*='telefono']",
                    "[class*='number']",
                ]:
                    el = await page.query_selector(phone_sel)
                    if el:
                        href = await el.get_attribute("href")
                        if href and href.startswith("tel:"):
                            return href.replace("tel:", "").strip()
                        text = (await el.inner_text()).strip()
                        if re.search(r'[6-9]\d{8}', text):
                            match = re.search(r'[6-9]\d{8}', text)
                            return match.group() if match else ""
                break
        except Exception:
            pass

    return ""


def extract_numeric_price(price_text: str) -> float | None:
    """Convert price string like '15.500 €' to float 15500.0"""
    try:
        cleaned = re.sub(r'[€$\s]', '', price_text)
        cleaned = cleaned.replace('.', '').replace(',', '.')
        return float(cleaned)
    except Exception:
        return None
