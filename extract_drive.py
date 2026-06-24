#!/usr/bin/env python3
import sys
if sys.version_info < (3, 11):
    print("❌ דרוש Python 3.11+")
    sys.exit(1)

import argparse
import re
from pathlib import Path
from datetime import datetime
import html
import urllib.request
from typing import Any, Callable, Optional, Dict, List, Tuple, Set
import concurrent.futures

# ===== Playwright =====
sync_playwright: Optional[Callable[..., Any]] = None
playwright_available = False
try:
    from playwright.sync_api import sync_playwright as _sync_playwright  # type: ignore
    sync_playwright = _sync_playwright
    playwright_available = True
except ImportError:
    pass

# ===== פונקציות בסיסיות =====

def clean_chat_text(text: str) -> str:
    """מנקה טקסט מהודעות צ'אט - מסיר תאריכים, שמות שולחים, וכו'"""
    lines = text.split('\n')
    cleaned_lines: List[str] = []
    
    for line in lines:
        if re.match(r'^\[\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}\]', line):
            parts = line.split(']', 1)
            if len(parts) > 1:
                line = parts[1].strip()
            else:
                continue
        
        line = re.sub(r'^[א-תa-zA-Z]+\s+[א-תa-zA-Z]+:', '', line)
        line = re.sub(r'^[א-תa-zA-Z]+:', '', line)
        line = re.sub(r'\[\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}\]', '', line)
        line = line.strip()
        if line:
            cleaned_lines.append(line)
    
    return '\n'.join(cleaned_lines)

def extract_folder_id(input_str: str) -> Optional[str]:
    """חלץ folder ID מקישור או זהה ID ישירות - תומך בכל הפורמטים"""
    input_str = input_str.strip()
    patterns = [
        r'/folders/([a-zA-Z0-9_-]+)',
        r'/drive/u/\d+/folders/([a-zA-Z0-9_-]+)',
        r'/embeddedfolderview\?id=([a-zA-Z0-9_-]+)',
        r'/open\?id=([a-zA-Z0-9_-]+)',
        r'id=([a-zA-Z0-9_-]+)',
        r'([a-zA-Z0-9_-]{20,})',
    ]
    for pattern in patterns:
        match = re.search(pattern, input_str)
        if match:
            return match.group(1)
    if re.match(r'^[a-zA-Z0-9_-]{20,}$', input_str):
        return input_str
    return None

def extract_all_links(text: str) -> List[Tuple[str, str]]:
    """מזהה את כל קישורי Drive בטקסט ומחזיר (קישור, הקשר)"""
    links: List[Tuple[str, str]] = []
    seen: Set[str] = set()
    
    link_pattern = r'(https?://(?:drive\.google\.com|www\.drive\.google\.com)[^\s"\'<>)]+)'
    
    for match in re.finditer(link_pattern, text):
        url = match.group(0).strip()
        url = re.sub(r'[.,;:!?]$', '', url)
        
        start = match.start()
        context_start = max(0, start - 100)
        context = text[context_start:start].strip()
        
        context = re.sub(r'\[\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}\]', '', context)
        context = re.sub(r'^[א-תa-zA-Z]+\s+[א-תa-zA-Z]+:', '', context)
        context = re.sub(r'^[א-תa-zA-Z]+:', '', context)
        context = context.strip()
        
        folder_id = extract_folder_id(url)
        
        if folder_id and folder_id not in seen:
            seen.add(folder_id)
            links.append((url, context))
    
    return links

def get_embedded_url(folder_id: str) -> str:
    return f'https://drive.google.com/embeddedfolderview?id={folder_id}#grid'

def get_folder_url(folder_id: str) -> str:
    return f'https://drive.google.com/drive/folders/{folder_id}'

def download_html(url: str) -> Optional[str]:
    """מוריד HTML של עמוד"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://www.google.com/',
        }
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.read().decode('utf-8', errors='ignore')
    except Exception:
        return None

def render_html_with_playwright(url: str) -> Optional[str]:
    """מרנדר עמוד עם Playwright - רק אם זמין"""
    if not playwright_available or sync_playwright is None:
        return None
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                locale='en-US',
                ignore_https_errors=True,
            )
            page = context.new_page()
            page.goto(url, wait_until='networkidle', timeout=45000)
            page.wait_for_timeout(2000)
            
            for _ in range(5):
                page.evaluate('window.scrollBy(0, document.body.scrollHeight)')
                page.wait_for_timeout(500)
            
            content = page.content()
            browser.close()
            return content
    except Exception:
        return None

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
    if href.startswith('file/d/'):
        return f'https://drive.google.com/{href}'
    return href

def parse_html(content: str) -> List[Tuple[str, str]]:
    """חלץ שמות וקישורים מ-HTML של תיקייה"""
    entries: List[Tuple[str, str]] = []
    seen_urls: Set[str] = set()
    
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
    
    json_pattern = re.compile(
        r'["\'](https?://drive\.google\.com/file/d/[A-Za-z0-9_-]+[^"\']*)["\']',
        re.IGNORECASE,
    )
    for match in json_pattern.finditer(content):
        url = normalize_drive_url(match.group(1))
        if url not in seen_urls:
            seen_urls.add(url)
            file_id = re.search(r'/d/([^/?]+)', url)
            if file_id:
                entries.append((f"File {file_id.group(1)[:8]}", url))
    
    return entries

def extract_folder_title(content: str) -> str:
    """חלץ את שם התיקייה מתוך HTML של העמוד"""
    title_match = re.search(r'<title>(.*?)</title>', content, re.IGNORECASE | re.DOTALL)
    if title_match:
        title = clean_text(title_match.group(1))
        if title:
            title = re.sub(r'\s*[-–—]\s*(?:Google Drive|Google\s*Drive|Drive)$', '', title)
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
                title = re.sub(r'\s*[-–—]\s*(?:Google Drive|Google\s*Drive|Drive)$', '', title)
                return title
    return ''

def extract_subfolder_links(content: str) -> List[Tuple[str, str]]:
    """חלץ ID ושמות של תיקיות משנה"""
    subfolders: List[Tuple[str, str]] = []
    seen_ids: Set[str] = set()
    
    anchor_pattern = re.compile(
        r'<a[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        re.DOTALL | re.IGNORECASE,
    )
    
    for match in anchor_pattern.finditer(content):
        href = match.group(1).strip()
        if not re.search(r'(?:/folders/|/drive/u/\d+/folders/|embeddedfolderview\?id=|^folders/|^embeddedfolderview\?id=)', href, re.IGNORECASE):
            continue
        
        folder_id = extract_folder_id(href)
        if folder_id and folder_id not in seen_ids:
            seen_ids.add(folder_id)
            title = extract_title_from_anchor(match.group(2)) or folder_id
            subfolders.append((folder_id, title))
    
    return subfolders

def parse_season_episode(name: str) -> Tuple[Optional[int], Optional[int]]:
    """מחלץ עונה ופרק משם - תומך בכל הפורמטים"""
    season: Optional[int] = None
    episode: Optional[int] = None
    
    match = re.search(r'עונה\s*[:]?\s*(\d+)\s*[-–—]?\s*(?:פרק|ep|episode)?\s*[:]?\s*(\d+)', name, re.IGNORECASE)
    if match:
        return int(match.group(1)), int(match.group(2))
    
    match = re.search(r'(?:S|ע)\s*(\d+)\s*[-–—]?\s*(?:E|פ)\s*(\d+)', name, re.IGNORECASE)
    if match:
        return int(match.group(1)), int(match.group(2))
    
    match = re.search(r'Season\s*(\d+)\s*Episode\s*(\d+)', name, re.IGNORECASE)
    if match:
        return int(match.group(1)), int(match.group(2))
    
    match = re.search(r'(?:עונה|Season)\s*[:]?\s*(\d+)', name, re.IGNORECASE)
    if match:
        season = int(match.group(1))
    
    match = re.search(r'(?:פרק|Episode|Ep|E)\s*[:]?\s*(\d+)', name, re.IGNORECASE)
    if match:
        episode = int(match.group(1))
    
    return season, episode

def collect_series_entries(
    folder_id: str,
    use_browser: bool = False,
    html_content: Optional[str] = None,
) -> Tuple[Dict[int, List[Tuple[str, str]]], str]:
    """אוסף קבצים ומארגן לפי עונות - כולל תיקיות משנה"""
    all_entries: Dict[int, List[Tuple[str, str]]] = {}
    visited: Set[str] = set()
    root_title = ''

    def process_folder(fid: str, prefix: str = "", html_text: Optional[str] = None) -> None:
        nonlocal root_title
        if fid in visited:
            return
        visited.add(fid)

        if html_text is None:
            html_text = download_html(get_embedded_url(fid))
            if not html_text:
                html_text = download_html(get_folder_url(fid))

        if html_text is None and use_browser:
            html_text = render_html_with_playwright(get_embedded_url(fid))
            if not html_text:
                html_text = render_html_with_playwright(get_folder_url(fid))

        if not html_text:
            return

        if fid == folder_id and not root_title:
            root_title = extract_folder_title(html_text) or folder_id

        # חלץ קבצים מהתיקייה הנוכחית
        folder_entries = parse_html(html_text)
        
        for name, url in folder_entries:
            season, _ = parse_season_episode(name)
            
            if season is None and prefix:
                season, _ = parse_season_episode(prefix)
            
            if season is None:
                season = 1
            
            if season not in all_entries:
                all_entries[season] = []
            all_entries[season].append((name, url))

        # חלץ תיקיות משנה (עונות) וטפל בהן
        subfolders = extract_subfolder_links(html_text)
        for subfolder_id, subfolder_title in subfolders:
            # בדוק אם זו תיקיית עונה
            season_match = re.search(r'עונה\s*(\d+)', subfolder_title, re.IGNORECASE)
            if season_match:
                season_num = int(season_match.group(1))
                # הורד את HTML של תיקיית העונה
                sub_html = download_html(get_embedded_url(subfolder_id))
                if not sub_html:
                    sub_html = download_html(get_folder_url(subfolder_id))
                if sub_html:
                    sub_entries = parse_html(sub_html)
                    for name, url in sub_entries:
                        _, episode = parse_season_episode(name)
                        if episode is None:
                            ep_match = re.search(r'פרק\s*(\d+)', name, re.IGNORECASE)
                            if ep_match:
                                episode = int(ep_match.group(1))
                        if season_num not in all_entries:
                            all_entries[season_num] = []
                        all_entries[season_num].append((name, url))
            else:
                # אם לא מזוהה כעונה, נכנס לתיקייה (רקורסיה)
                process_folder(subfolder_id, subfolder_title)

    process_folder(folder_id, html_text=html_content)
    return all_entries, root_title

def calculate_optimal_workers(links: List[Tuple[str, str]]) -> int:
    """מחשב מספר חוטים אופטימלי לפי כמות הקישורים"""
    count = len(links)
    
    if count <= 3:
        return 1
    elif count <= 10:
        return 3
    elif count <= 30:
        return 5
    elif count <= 60:
        return 8
    else:
        return 12

def process_series_parallel(
    folder_links: List[Tuple[str, str]], 
    use_browser: bool = False, 
    max_workers: Optional[int] = None
) -> Dict[str, Dict[int, List[Tuple[str, str]]]]:
    """מעבד סדרות במקביל - מחזיר מילון עם שם הסדרה והעונות"""
    if max_workers is None:
        max_workers = calculate_optimal_workers(folder_links)
    
    all_series: Dict[str, Dict[int, List[Tuple[str, str]]]] = {}
    
    print(f"\n🚀 מעבד {len(folder_links)} סדרות במקביל ({max_workers} חוטים)...")
    
    def process_one(link_info: Tuple[str, str]) -> Tuple[str, Dict[int, List[Tuple[str, str]]]]:
        url, context = link_info
        fid = extract_folder_id(url)
        if not fid:
            return context or "סדרה ללא שם", {}
        
        entries, title = collect_series_entries(fid, use_browser)
        series_name = title or context or f"סדרה {fid[:8]}"
        
        # נקה את שם הסדרה
        series_name = re.sub(r'\[\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}\]', '', series_name)
        series_name = re.sub(r'^[א-תa-zA-Z]+\s+[א-תa-zA-Z]+:', '', series_name)
        series_name = series_name.strip()
        
        if not series_name:
            series_name = f"סדרה {fid[:8]}"
        
        return series_name, entries
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_info = {
            executor.submit(process_one, link_info): link_info 
            for link_info in folder_links
        }
        
        completed = 0
        total = len(folder_links)
        
        for future in concurrent.futures.as_completed(future_to_info):
            completed += 1
            link_info = future_to_info[future]
            try:
                series_name, entries = future.result()
                if entries:
                    all_series[series_name] = entries
                    total_files = sum(len(files) for files in entries.values())
                    print(f"   ✅ [{completed}/{total}] סיים: {series_name} ({total_files} קבצים)")
                else:
                    print(f"   ⚠️ [{completed}/{total}] לא נמצאו קבצים: {series_name}")
            except Exception as e:
                print(f"   ❌ [{completed}/{total}] שגיאה: {e}")
    
    return all_series

def format_series_output(all_series: Dict[str, Dict[int, List[Tuple[str, str]]]]) -> str:
    """מייצר את הפלט בפורמט נקי - עם כותרות סדרות"""
    output_parts: List[str] = []
    
    for series_name in sorted(all_series.keys()):
        entries = all_series[series_name]
        
        # כותרת הסדרה
        output_parts.append(f"🎬 {series_name}")
        output_parts.append("=" * 40)
        
        for season in sorted(entries.keys()):
            output_parts.append(f"\nעונה {season}")
            
            files = sorted(entries[season], key=lambda x: parse_season_episode(x[0])[1] or 0)
            
            for name, url in files:
                _, episode = parse_season_episode(name)
                if episode:
                    output_parts.append(f"פרק {episode}")
                else:
                    clean_name = re.sub(r'עונה\s*\d+\s*', '', name, flags=re.IGNORECASE).strip()
                    clean_name = re.sub(r'פרק\s*\d+\s*', '', clean_name, flags=re.IGNORECASE).strip()
                    if clean_name:
                        output_parts.append(clean_name)
                    else:
                        output_parts.append("קובץ")
                output_parts.append(url)
        
        output_parts.append("")  # שורה ריקה בין סדרות
    
    return '\n'.join(output_parts)

def save_series_txt(all_series: Dict[str, Dict[int, List[Tuple[str, str]]]]) -> str:
    """שומר את הפלט בקובץ"""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = f"series_{timestamp}.txt"
    
    output = format_series_output(all_series)
    
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(output)
    
    return output_file

def get_series_text_from_user() -> str:
    """מקבל טקסט מהמשתמש"""
    print("📝 הדבק את הטקסט (כולל תקציר, קישורים לעונות וכו'):")
    print("   (לחץ Enter פעמיים לסיום)\n")
    
    lines: List[str] = []
    empty_count = 0
    
    while True:
        try:
            line = input()
        except EOFError:
            break
            
        if line.strip() == "":
            empty_count += 1
            if empty_count >= 2:
                break
        else:
            empty_count = 0
        
        lines.append(line)
    
    return '\n'.join(lines)

def main():
    parser = argparse.ArgumentParser(
        description='מחלץ סדרות מ-Google Drive - עם זיהוי שמות תיקיות',
        epilog='דוגמה: python sdarot.py'
    )
    parser.add_argument('--browser', action='store_true', help='השתמש ב-Playwright (לעמודים דינמיים)')
    parser.add_argument('-t', '--text', help='טקסט הסדרה (תקציר + קישורים)')
    parser.add_argument('-o', '--output', help='קובץ פלט מותאם אישית')
    parser.add_argument('-w', '--workers', type=int, help='מספר חוטי עיבוד (ברירת מחדל: אוטומטי לפי כמות)')
    args = parser.parse_args()

    # שלב 1: קבל טקסט
    if args.text:
        series_text = args.text
        print("📝 קורא מטקסט שהוכנס")
    else:
        print("=" * 60)
        print("מחלץ סדרות מ-Google Drive - חכם ומהיר!")
        print("=" * 60)
        print()
        series_text = get_series_text_from_user()
    
    if not series_text.strip():
        print("❌ לא הוכנס טקסט")
        return 1
    
    # שלב 2: נקה טקסט מצ'אט
    cleaned_text = clean_chat_text(series_text)
    
    # שלב 3: מצא קישורים
    print("\n🔍 מזהה קישורים...")
    
    folder_links = extract_all_links(cleaned_text)
    
    if not folder_links:
        print("❌ לא נמצאו קישורים תקינים")
        return 1
    
    print(f"✅ נמצאו {len(folder_links)} קישורים")
    
    # שלב 4: קבע מספר חוטים אוטומטית
    if args.workers:
        workers = args.workers
        print(f"📊 משתמש ב-{workers} חוטים לפי בקשתך")
    else:
        workers = calculate_optimal_workers(folder_links)
        print(f"📊 משתמש ב-{workers} חוטים אוטומטית")
    
    # שלב 5: עבד תיקיות במקביל
    use_browser = args.browser or playwright_available
    all_series = process_series_parallel(folder_links, use_browser, workers)
    
    if not all_series:
        print("❌ לא נמצאו קבצים. סיבות אפשריות:")
        print("   - התיקייה פרטית או דורשת התחברות")
        print("   - Google שינתה את מבנה העמוד")
        print("   - העמוד דורש JavaScript (השתמש ב--browser)")
        if not playwright_available:
            print("   - Playwright לא מותקן. התקן: pip install playwright")
        return 1
    
    # שלב 6: שמור
    total_series = len(all_series)
    total_files = sum(sum(len(files) for files in entries.values()) for entries in all_series.values())
    
    if args.output:
        output_file = args.output
        output = format_series_output(all_series)
        Path(args.output).write_text(output, encoding='utf-8')
    else:
        output_file = save_series_txt(all_series)
    
    print()
    print("=" * 60)
    print("✅ הצלחה!")
    print("=" * 60)
    print(f"📊 נמצאו: {total_series} סדרות, {total_files} קבצים")
    print(f"💾 שמור ל: {output_file}")
    print()
    print("תצוגה מקדימה:")
    output_preview = format_series_output(all_series)
    preview_lines = output_preview.splitlines()[:20]
    for line in preview_lines:
        if line.strip():
            print(f"  {line}")
    output_lines = output_preview.splitlines()
    if len(output_lines) > 20:
        print(f"  ... ועוד {len(output_lines) - 20} שורות")
    print()
    print("=" * 60)
    
    return 0

if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\n⚠️ בוטל על ידי המשתמש")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ שגיאה בלתי צפויה: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)