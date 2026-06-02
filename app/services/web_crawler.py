import ipaddress
import re
import socket
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Iterable
from urllib.parse import urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

REMOVABLE_SELECTORS = [
    "script",
    "style",
    "noscript",
    "svg",
    "canvas",
    "nav",
    "footer",
    "header",
    "form",
    "iframe",
    "[role='alert']",
    "[role='banner']",
    "[role='dialog']",
    "[role='alertdialog']",
    "[aria-modal='true']",
    "[class*='cookie' i]",
    "[id*='cookie' i]",
    "[class*='consent' i]",
    "[id*='consent' i]",
]


@dataclass(frozen=True)
class CrawlerConfig:
    max_pages: int = 10
    max_depth: int = 1
    page_timeout_seconds: int = 25
    wait_after_load_seconds: float = 1.0
    max_chars_per_page: int = 16000
    max_total_chars: int = 80000
    user_agent: str = DEFAULT_USER_AGENT
    headless: bool = True


@dataclass
class CrawledPage:
    url: str
    title: str = ""
    text: str = ""
    links: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class CrawlResult:
    start_url: str
    pages: list[CrawledPage]

    @property
    def content(self) -> str:
        chunks = []
        for page in self.pages:
            if page.text:
                chunks.append(f"=== PAGE: {page.url} ===\n{page.text}")
        return "\n\n".join(chunks).strip()


def normalize_url(url: str, base_url: str | None = None) -> str:
    absolute = urljoin(base_url, url.strip()) if base_url else url.strip()
    absolute, _fragment = urldefrag(absolute)
    parsed = urlparse(absolute)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return parsed._replace(path=parsed.path or "/", query=parsed.query).geturl()


def validate_public_url(url: str) -> str:
    normalized = normalize_url(url)
    if not normalized:
        raise ValueError("Only absolute http(s) URLs are supported.")

    parsed = urlparse(normalized)
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("URL hostname is missing.")

    try:
        addresses = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise ValueError(f"Could not resolve hostname: {hostname}") from exc

    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
            raise ValueError("Private, local, and reserved network addresses cannot be crawled.")

    return normalized


def same_site(url: str, start_url: str) -> bool:
    current = urlparse(url)
    start = urlparse(start_url)
    if not current.hostname or not start.hostname:
        return False
    current_host = current.hostname.lower().removeprefix("www.")
    start_host = start.hostname.lower().removeprefix("www.")
    return current_host == start_host


def clean_text(text: str) -> str:
    lines = []
    for line in text.splitlines():
        cleaned = re.sub(r"\s+", " ", line).strip()
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines).strip()


def create_driver(config: CrawlerConfig):
    options = Options()
    if config.headless:
        options.add_argument("--headless=new")
    options.add_argument("--window-size=1365,900")
    options.add_argument("--lang=en-US,en")
    options.add_argument(f"--user-agent={config.user_agent}")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-gpu")
    options.add_argument("--log-level=3")
    options.page_load_strategy = "eager"
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(config.page_timeout_seconds)
    return driver


def wait_for_page(driver, timeout: int) -> None:
    try:
        WebDriverWait(driver, timeout).until(
            lambda current_driver: current_driver.execute_script("return document.readyState")
            in {"interactive", "complete"}
        )
    except TimeoutException:
        pass


def expand_visible_content(driver) -> None:
    selectors = [
        "[aria-expanded='false']",
        "details:not([open]) summary",
        "button[class*='accordion' i]",
        "button[class*='expand' i]",
    ]
    clicked = 0
    for selector in selectors:
        for element in driver.find_elements(By.CSS_SELECTOR, selector)[:8]:
            if clicked >= 12:
                return
            try:
                if element.is_displayed() and element.is_enabled():
                    driver.execute_script("arguments[0].click();", element)
                    clicked += 1
                    time.sleep(0.12)
            except WebDriverException:
                continue


def dismiss_common_overlays(driver) -> None:
    candidates = [
        "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'accept')]",
        "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'agree')]",
        "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'got it')]",
        "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'close')]",
        "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'dismiss')]",
    ]
    clicked = 0
    for xpath in candidates:
        for element in driver.find_elements(By.XPATH, xpath)[:3]:
            if clicked >= 3:
                return
            try:
                if element.is_displayed() and element.is_enabled():
                    driver.execute_script("arguments[0].click();", element)
                    clicked += 1
                    time.sleep(0.15)
            except WebDriverException:
                continue


def scroll_page(driver) -> None:
    try:
        last_height = driver.execute_script("return document.body.scrollHeight") or 0
        for _ in range(4):
            driver.execute_script("window.scrollBy(0, Math.max(500, window.innerHeight * 0.85));")
            time.sleep(0.3)
            next_height = driver.execute_script("return document.body.scrollHeight") or 0
            if next_height == last_height:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(0.25)
                final_height = driver.execute_script("return document.body.scrollHeight") or 0
                if final_height != last_height:
                    last_height = final_height
                    continue
                break
            last_height = next_height
        driver.execute_script("window.scrollTo(0, 0);")
    except WebDriverException:
        return


def extract_links(soup: BeautifulSoup, current_url: str, start_url: str) -> list[str]:
    links = []
    seen = set()
    for anchor in soup.select("a[href]"):
        normalized = normalize_url(anchor.get("href", ""), current_url)
        if not normalized or normalized in seen or not same_site(normalized, start_url):
            continue
        parsed = urlparse(normalized)
        if parsed.path.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".pdf", ".zip", ".mp4", ".mov")):
            continue
        seen.add(normalized)
        links.append(normalized)
    return links


def extract_page(html: str, current_url: str, start_url: str, max_chars: int) -> CrawledPage:
    soup = BeautifulSoup(html or "", "html.parser")
    title = clean_text(soup.title.get_text(" ", strip=True)) if soup.title else ""
    for selector in REMOVABLE_SELECTORS:
        for node in soup.select(selector):
            node.decompose()

    main = (
        soup.select_one("main")
        or soup.select_one("article")
        or soup.select_one("[role='main']")
        or soup.body
        or soup
    )
    text = clean_text(main.get_text("\n", strip=True))
    if len(text) < 250 and soup.body:
        text = clean_text(soup.body.get_text("\n", strip=True))
    text = text[:max_chars]
    links = extract_links(soup, current_url, start_url)
    return CrawledPage(url=current_url, title=title, text=text, links=links)


def crawl_website(url: str, config: CrawlerConfig | None = None) -> CrawlResult:
    config = config or CrawlerConfig()
    start_url = validate_public_url(url)
    queue = deque([(start_url, 0)])
    visited: set[str] = set()
    pages: list[CrawledPage] = []
    total_chars = 0

    driver = create_driver(config)
    try:
        while queue and len(pages) < config.max_pages and total_chars < config.max_total_chars:
            current_url, depth = queue.popleft()
            if current_url in visited:
                continue
            visited.add(current_url)

            try:
                driver.get(current_url)
                wait_for_page(driver, config.page_timeout_seconds)
                time.sleep(config.wait_after_load_seconds)
                dismiss_common_overlays(driver)
                expand_visible_content(driver)
                scroll_page(driver)
                page = extract_page(driver.page_source, driver.current_url or current_url, start_url, config.max_chars_per_page)
            except Exception as exc:
                page = CrawledPage(url=current_url, error=str(exc))

            pages.append(page)
            total_chars += len(page.text or "")

            if depth >= config.max_depth:
                continue
            for link in page.links:
                if link not in visited and len(queue) + len(pages) < config.max_pages:
                    queue.append((link, depth + 1))
    finally:
        driver.quit()

    return CrawlResult(start_url=start_url, pages=pages)


def format_crawl_content(result: CrawlResult) -> str:
    return result.content


def crawl_many(urls: Iterable[str], config: CrawlerConfig | None = None) -> list[CrawlResult]:
    return [crawl_website(url, config=config) for url in urls]
