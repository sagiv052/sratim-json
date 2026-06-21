#!/usr/bin/env python3

import sys

# דרוש Python 3.11+
if sys.version_info < (3, 11):
    print("❌ דרוש Python 3.11 או גבוה יותר!")
    print(f"🔍 גרסה נוכחית: Python {sys.version}")
    print("📥 הורד: https://www.python.org/downloads/")
    sys.exit(1)

import argparse
import re
from pathlib import Path
from datetime import datetime
import html
import urllib.request
import urllib.error
from typing import Any, Callable, Optional

sync_playwright: Optional[Callable[..., Any]] = None
playwright_available = False
try:
    from playwright.sync_api import sync_playwright as _sync_playwright
    sync_playwright = _sync_playwright
    playwright_available = True
except ImportError:
    pass


def extract_folder_id(input_str: str) -> str:
    """חלץ folder ID מקישור או זהה ID ישירות"""
    input_str = input_str.strip()

    patterns = [
        r'/folders/([a-zA-Z0-9_-]+)',
        r'/drive/u/\d+/folders/([a-zA-Z0-9_-]+)',
        r'/embeddedfolderview\?id=([a-zA-Z0-9_-]+)',
        r'/open\?id=([a-zA-Z0-9_-]+)',
        r'id=([a-zA-Z0-9_-]+)',
    ]

    for pattern in patterns:
        match = re.search(pattern, input_str)
        if match:
            return match.group(1)

    if re.match(r'^[a-zA-Z0-9_-]+$', input_str):
        return input_str

    raise ValueError(f"Invalid folder ID or URL: {input_str}")


def get_embedded_url(folder_id: str) -> str:
    return f'https://drive.google.com/embeddedfolderview?id={folder_id}#grid'


def get_folder_url(folder_id: str) -> str:
    return f'https://drive.google.com/drive/folders/{folder_id}'


def download_html(folder_id: str) -> Optional[str]:
    url = get_embedded_url(folder_id)
    print(f"📥 הורדה מ: {url}")
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://www.google.com/',
        }
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as response:
            content = response.read().decode('utf-8', errors='ignore')
            print("✓ הוררה בהצלחה")
            return content
    except urllib.error.URLError as e:
        print(f"❌ הורדה נכשלה: {e}")
        print(f"\n⚠️  דרך חלופית:")
        print(f"1. פתח בדפדפן: {url}")
        print(f"2. שמור דף (Ctrl+S)")
        print(f"3. הרץ: python extract_drive.py {folder_id} -f saved_file.html")
        return None


def render_html_with_playwright(folder_id: str) -> Optional[str]:
    if not playwright_available or sync_playwright is None:
        return None
    playwright_launcher: Callable[..., Any] = sync_playwright

    def capture_from_url(url: str) -> Optional[str]:
        print(f"🌐 רינדור עמוד: {url}")
        try:
            with playwright_launcher() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
                    locale='en-US',
                    ignore_https_errors=True,
                )
                page = context.new_page()
                page.goto(url, wait_until='networkidle', timeout=45000)
                page.wait_for_selector('body', state='attached', timeout=20000)
                page.wait_for_timeout(1500)

                load_selectors = [
                    'a[href*="/file/d/"], a[href*="/open?id="], a[href*="/uc?export=download"], .flip-entry, .flip-entries',
                    'div[aria-label*="File"] a[href*="/file/d/"], div[aria-label*="File"] a[href*="/open?id="]',
                ]

                page_is_ready = False
                for selector in load_selectors:
                    try:
                        page.wait_for_selector(selector, state='attached', timeout=12000)
                        page_is_ready = True
                        break
                    except Exception:
                        continue

                for _ in range(10):
                    page.evaluate('window.scrollBy(0, document.body.scrollHeight)')
                    page.wait_for_timeout(500)

                content = page.content()
                if page_is_ready:
                    print("✓ תוכן נלכד בהצלחה")
                else:
                    print("⚠️ תוכן נלכד")
                return content
        except Exception as e:
            print(f"❌ כשל בריינדור: {e}")
            return None

    content = capture_from_url(get_embedded_url(folder_id))
    if content:
        return content

    return capture_from_url(get_folder_url(folder_id))


def clean_text(html_text: str) -> str:
    text = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', html_text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    return html.unescape(re.sub(r'\s+', ' ', text).strip())


def extract_title_from_anchor(anchor_html: str) -> str:
    title_match = re.search(
        r'<div[^>]*class=["\'](?:flip-entry-title|entry-title|title)["\'][^>]*>(.*?)</div>',
        anchor_html,
        re.IGNORECASE | re.DOTALL,
    )
    if title_match:
        return clean_text(title_match.group(1))

    alt_match = re.search(r'<img[^>]*alt=["\']([^"\']+)["\']', anchor_html, re.IGNORECASE)
    if alt_match:
        return html.unescape(alt_match.group(1).strip())

    title_attr = re.search(r'title=["\']([^"\']+)["\']', anchor_html, re.IGNORECASE)
    if title_attr:
        return html.unescape(title_attr.group(1).strip())

    return clean_text(anchor_html)


def normalize_drive_url(href: str) -> str:
    href = href.strip()
    if href.startswith('/'):
        return f'https://drive.google.com{href}'
    if href.startswith('drive.google.com/'):
        return f'https://{href}'
    return href


def parse_html(content: str) -> list[tuple[str, str]]:
    """חלץ שמות וקישורים מ-HTML של תיקייה"""
    entries: list[tuple[str, str]] = []
    seen_urls: set[str] = set()

    # שלב 1: חלץ קישורים מ-anchor tags
    file_pattern = re.compile(
        r'<a[^>]*href=["\']([^"\']*(?:drive\.google\.com)?/file/d/[A-Za-z0-9_-]+[^"\']*)["\'][^>]*>(.*?)</a>',
        re.DOTALL | re.IGNORECASE,
    )

    for match in file_pattern.finditer(content):
        url = normalize_drive_url(match.group(1))
        anchor_html = match.group(2)
        title = extract_title_from_anchor(anchor_html)
        
        if title and url not in seen_urls:
            seen_urls.add(url)
            entries.append((title, url))

    if entries:
        return entries

    # שלב 2: חלץ מ-flip-entry blocks
    entry_block_pattern = re.compile(
        r'<div[^>]*class=["\']?(?:flip-entry|drive-item)["\']?[^>]*>(.*?)(?=<div[^>]*class=["\']?(?:flip-entry|drive-item)["\']?|$)',
        re.DOTALL | re.IGNORECASE,
    )

    for match in entry_block_pattern.finditer(content):
        block = match.group(1)
        # דלג אם זה תיקייה
        if 'folder' in block.lower():
            continue
        
        href_match = re.search(
            r'href=["\']([^"\']*(?:drive\.google\.com)?/file/d/[A-Za-z0-9_-]+[^"\']*)["\']',
            block,
            re.IGNORECASE,
        )
        title = extract_title_from_anchor(block)
        if href_match and title:
            url = normalize_drive_url(href_match.group(1))
            if url not in seen_urls:
                seen_urls.add(url)
                entries.append((title, url))

    if entries:
        return entries

    # שלב 3: חלץ קישורים מ-JSON
    json_pattern = re.compile(
        r'["\'](https?://drive\.google\.com/file/d/[A-Za-z0-9_-]+[^"\']*)["\']',
        re.IGNORECASE,
    )
    
    for match in json_pattern.finditer(content):
        url = normalize_drive_url(match.group(1))
        # חלץ שם קובץ מהקישור
        file_id_match = re.search(r'/file/d/([A-Za-z0-9_-]+)', url)
        if file_id_match:
            file_id = file_id_match.group(1)
            if url not in seen_urls:
                # Try to find filename nearby in content
                title = None
                idx = content.find(url)
                if idx > 0:
                    # Look backwards for a name
                    search_start = max(0, idx - 500)
                    context = content[search_start:idx + 100]
                    name_match = re.search(r'["\']([^"\']{1,100})["\']', context)
                    if name_match:
                        title = clean_text(name_match.group(1))
                
                if not title:
                    title = f"File {file_id[:8]}"
                
                if title and url not in seen_urls:
                    seen_urls.add(url)
                    entries.append((title, url))

    if entries:
        return entries

    # חלוף: פורמט טקסט קדום
    lines = content.splitlines()
    current_name = None

    for line in lines:
        if '- link "Video ' in line and ' [ref=' in line:
            try:
                start = line.index('- link "Video ') + len('- link "Video ')
                end = line.index('" [ref=', start)
                current_name = line[start:end].strip()
            except (ValueError, IndexError):
                current_name = None

        elif current_name and '- /url: https://drive.google.com/file/d/' in line:
            try:
                start = line.index('- /url: ') + len('- /url: ')
                url = line[start:].strip()
                if url.startswith('https://drive.google.com/file/d/') and url not in seen_urls:
                    seen_urls.add(url)
                    entries.append((current_name, url))
                    current_name = None
            except (ValueError, IndexError):
                current_name = None

    return entries


def extract_folder_title(content: str) -> str:
    """חלץ את שם התיקייה מתוך HTML של העמוד"""
    title_match = re.search(r'<title>(.*?)</title>', content, re.IGNORECASE | re.DOTALL)
    if title_match:
        title = clean_text(title_match.group(1))
        if title:
            return title

    patterns = [
        r'<(?:div|h1|span)[^>]*\bclass=["\'][^"\']*(?:title|folder-title|drive-title|doc-title|entry-title)[^"\']*["\'][^>]*>(.*?)</(?:div|h1|span)>',
        r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
        if match:
            title = clean_text(match.group(1))
            if title:
                return title

    return ''


def extract_subfolder_links(content: str) -> list[tuple[str, str]]:
    """חלץ ID ושמות של תיקיות משנה"""
    subfolders: list[tuple[str, str]] = []
    seen_ids: set[str] = set()
    anchor_pattern = re.compile(
        r'<a[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        re.DOTALL | re.IGNORECASE,
    )

    for match in anchor_pattern.finditer(content):
        href = match.group(1).strip()
        if not re.search(r'(?:/folders/|/drive/u/\d+/folders/|embeddedfolderview\?id=|^folders/|^embeddedfolderview\?id=)', href, re.IGNORECASE):
            continue

        try:
            subfolder_id = extract_folder_id(href)
        except ValueError:
            continue

        if subfolder_id in seen_ids:
            continue

        seen_ids.add(subfolder_id)
        title = extract_title_from_anchor(match.group(2)) or subfolder_id
        subfolders.append((subfolder_id, title))

    return subfolders


def collect_folder_entries(
    folder_id: str,
    use_browser: bool = False,
    html_content: Optional[str] = None,
) -> tuple[list[tuple[str, str]], str]:
    """אסוף קבצים מתיקייה וסרוק תיקיות משנה"""
    entries: list[tuple[str, str]] = []
    visited: set[str] = set()
    root_title = ''

    def process_folder(fid: str, prefix: str = "", html_text: Optional[str] = None) -> None:
        nonlocal root_title
        if fid in visited:
            return
        visited.add(fid)

        print(f"📂 עיבוד תיקייה: {fid} (prefix: {prefix or '/'})")

        if html_text is None:
            html_text = download_html(fid)
            browser_used = False
        else:
            browser_used = False

        if html_text is None and use_browser:
            html_text = render_html_with_playwright(fid)
            browser_used = True

        if not html_text:
            return

        if fid == folder_id and not root_title:
            root_title = extract_folder_title(html_text) or folder_id

        # חלץ קבצים
        folder_entries = parse_html(html_text)
        if prefix:
            folder_entries = [(f"{prefix}{name}", url) for name, url in folder_entries]
        entries.extend(folder_entries)

        # סרוק תיקיות משנה
        subfolders = extract_subfolder_links(html_text)

        # אם אין תוצאות ברורות, נסה עוד פעם עם רינדור דפדפן
        if use_browser and not browser_used and (not folder_entries or not subfolders):
            browser_html_content = render_html_with_playwright(fid)
            if browser_html_content and browser_html_content != html_text:
                print("⚠️ לא נמצאו מספיק פריטים ב-HTML הסטטי, מנסה שוב עם דפדפן...")
                html_text = browser_html_content
                browser_used = True
                folder_entries = parse_html(html_text)
                if prefix:
                    folder_entries = [(f"{prefix}{name}", url) for name, url in folder_entries]
                entries.extend(folder_entries)
                subfolders = extract_subfolder_links(html_text)

        for subfolder_id, subfolder_title in subfolders:
            new_prefix = f"{prefix}{subfolder_title}/"
            process_folder(subfolder_id, new_prefix)

    process_folder(folder_id, html_text=html_content)
    return entries, root_title


def save_txt(entries: list[tuple[str, str]], folder_id: str, root_title: str) -> str:
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = f"drive_files_{folder_id}_{timestamp}.txt"

    grouped_entries: dict[str, list[tuple[str, str]]] = {}
    for name, url in entries:
        if '/' in name:
            folder_path, file_name = name.rsplit('/', 1)
        else:
            folder_path, file_name = '.', name
        grouped_entries.setdefault(folder_path, []).append((file_name, url))

    with open(output_file, 'w', encoding='utf-8') as f:
        if root_title:
            f.write(f'תיקייה עליונה: {root_title}\n\n')

        for folder_path in sorted(grouped_entries):
            if folder_path == '.':
                f.write('תיקייה ראשית:\n')
            else:
                f.write(f'תיקייה משנה: {folder_path}\n')

            f.write('FILE_NAME\tFILE_URL\n')
            for file_name, url in grouped_entries[folder_path]:
                f.write(f'{file_name}\t{url}\n')
            f.write('\n')

    return output_file


def main():
    parser = argparse.ArgumentParser(
        description='מחלץ תיקיות Google Drive',
        epilog='דוגמה: python extract_drive.py https://drive.google.com/drive/folders/FOLDER_ID',
    )
    parser.add_argument('folder', nargs='?', help='קישור או ID של תיקיית Drive')
    parser.add_argument('-f', '--file', dest='html_file', help='קרא HTML מקובץ')
    parser.add_argument('--browser', action='store_true', help='השתמש בדפדפן Playwright')
    args = parser.parse_args()

    folder_input = args.folder
    if not folder_input:
        print("=" * 60)
        print("מחלץ תיקיות Google Drive")
        print("=" * 60)
        print()
        folder_input = input("הכנס קישור או ID: ").strip()
        if not folder_input:
            print("❌ חובה להכניס קישור או ID!")
            return 1

    try:
        folder_id = extract_folder_id(folder_input)
        print()
        print("=" * 60)
        print(f"✓ ID: {folder_id}")
        print("=" * 60)
        print()

        html_content: str = ""

        if args.html_file:
            html_file = args.html_file
            if Path(html_file).exists():
                print(f"📄 קריאת HTML מ: {html_file}")
                html_content = Path(html_file).read_text(encoding='utf-8', errors='ignore')
            else:
                print(f"❌ קובץ לא נמצא: {html_file}")
                return 1

        if args.html_file:
            print()
            print("🔍 ניתוח קבצים מ-HTML...")
            entries = parse_html(html_content)
            root_title = extract_folder_title(html_content) or folder_id
            if entries:
                subfolders = extract_subfolder_links(html_content)
                for subfolder_id, subfolder_title in subfolders:
                    print(f"📂 נמצאה תיקייה משנה: {subfolder_title}")
                    sub_entries, _ = collect_folder_entries(
                        subfolder_id,
                        use_browser=args.browser or playwright_available,
                    )
                    entries.extend((f"{subfolder_title}/{name}", url) for name, url in sub_entries)
        else:
            entries, root_title = collect_folder_entries(
                folder_id,
                use_browser=args.browser or playwright_available,
            )

        # אם לא נמצאו קבצים, נסה עם דפדפן
        if not entries and playwright_available and not args.html_file:
            print()
            print("⚠️  לא נמצאו קבצים, משתמש בדפדפן...")
            print()
            entries, root_title = collect_folder_entries(
                folder_id,
                use_browser=True,
            )

        if not entries:
            print("❌ לא נמצאו קבצים. סיבות אפשריות:")
            print("   - התיקייה פרטית או דורשת התחברות")
            print("   - Google שינתה את מבנה העמוד")
            print("   - העמוד דורש JavaScript")
            if not playwright_available:
                print("   - Playwright לא מותקן. התקן: pip install playwright")
            return 1
        
        output_file = save_txt(entries, folder_id, root_title)
        
        print()
        print("=" * 60)
        print("✅ הצלחה!")
        print("=" * 60)
        print(f"📊 נמצא: {len(entries)} קבצים")
        print(f"💾 שמור ל: {output_file}")
        print()
        print("תצוגה מקדימה:")
        for i, (name, _) in enumerate(entries[:5], 1):
            print(f"  {i}. {name}")
        if len(entries) > 5:
            print(f"  ... ו-{len(entries) - 5} נוספים")
        print()
        print("=" * 60)
        
        return 0
    
    except ValueError as e:
        print(f"❌ שגיאה: {e}")
        return 1
    except Exception as e:
        print(f"❌ שגיאה בלתי צפויה: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())