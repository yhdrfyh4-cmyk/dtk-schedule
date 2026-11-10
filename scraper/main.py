import os
import re
import sys
import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
import requests
import pdfplumber

try:
    from docx import Document
except ImportError:
    Document = None

sys.stdout.reconfigure(encoding='utf-8')

DEFAULT_GROUP = "20"
PUBLIC_KEY = "https://disk.yandex.ru/d/dixfX_wF6wcCmg"
YANDEX_API = "https://cloud-api.yandex.net/v1/disk/public/resources"
LOCAL_DATA_FILE = Path(__file__).parent.parent / "data" / "schedule.json"

MONTH_NAMES_RU = [
    "", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"
]

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

def parse_date_from_filename(filename):
    clean = re.sub(r'\s+', '', filename)
    m = re.search(r'(\d{1,2})[\.\,]+(\d{1,2})[\.\,]*(\d{2,4})', clean)
    if m:
        d = int(m.group(1))
        m_num = int(m.group(2))
        y = int(m.group(3))
        if y < 100:
            y += 2000
        try:
            return datetime(y, m_num, d).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None

def get_disk_items(path=None):
    params = {'public_key': PUBLIC_KEY, 'limit': 100}
    if path:
        params['path'] = path
    r = requests.get(YANDEX_API, params=params, timeout=15)
    if r.status_code == 200:
        return r.json().get('_embedded', {}).get('items', [])
    return []

def download_file(item_path, save_to):
    r = requests.get(f"{YANDEX_API}/download", params={'public_key': PUBLIC_KEY, 'path': item_path}, timeout=15)
    if r.status_code == 200:
        d_url = r.json().get('href')
        res = requests.get(d_url, timeout=30)
        if res.status_code == 200:
            with open(save_to, 'wb') as f:
                f.write(res.content)
            return True
    return False

def parse_pdf_all_groups(filepath):
    """Парсит PDF расписание для ВСЕХ групп колледжа."""
    groups_data = {}
    try:
        with pdfplumber.open(filepath) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                for table in tables:
                    current_group = None
                    for row in table:
                        if not row:
                            continue
                        first_cell = str(row[0] or '').strip().replace('\n', ' ')
                        if first_cell and not re.match(r'^(№|группа|курс|\d+\.)$', first_cell, re.I):
                            # Новая группа
                            current_group = first_cell
                            if current_group not in groups_data:
                                groups_data[current_group] = []
                        
                        if current_group:
                            num_str = ''
                            for cell in row[1:3]:
                                if cell and re.search(r'\d', str(cell)):
                                    m = re.search(r'(\d+)', str(cell))
                                    if m:
                                        num_str = m.group(1)
                                        break
                            if not num_str:
                                continue
                            
                            lesson_num = int(num_str)
                            cells = [str(c).replace('\n', ' ').strip() for c in row[1:] if c and str(c).strip()]
                            if len(cells) >= 2:
                                subject = cells[1]
                                teacher = cells[2] if len(cells) >= 3 else ""
                                room = cells[3] if len(cells) >= 4 else ""
                                if not room and len(cells) >= 3 and re.search(r'\d+|зал|спорт', cells[-1], re.I):
                                    room = cells[-1]
                                    if teacher == room:
                                        teacher = ""
                                
                                if subject and not subject.isdigit():
                                    groups_data[current_group].append({
                                        "number": lesson_num,
                                        "name": subject,
                                        "teacher": teacher,
                                        "room": room
                                    })
    except Exception as e:
        log(f"Ошибка парсинга PDF {filepath}: {e}")
    
    # Очистка дубликатов
    for g, lessons in groups_data.items():
        unique = {}
        for l in lessons:
            unique[l['number']] = l
        groups_data[g] = sorted(unique.values(), key=lambda x: x['number'])
    
    return groups_data

def parse_docx_all_groups(filepath):
    """Парсит DOCX расписание для ВСЕХ групп колледжа."""
    if Document is None:
        return {}
    groups_data = {}
    try:
        doc = Document(filepath)
        for table in doc.tables:
            current_group = None
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells]
                if not cells:
                    continue
                first_cell = cells[0]
                if first_cell and not re.match(r'^(№|группа|курс|\d+\.)$', first_cell, re.I):
                    current_group = first_cell
                    if current_group not in groups_data:
                        groups_data[current_group] = []
                
                if current_group:
                    non_empty = [c for c in cells[1:] if c]
                    if len(non_empty) >= 2:
                        m = re.search(r'(\d+)', non_empty[0])
                        if m:
                            num = int(m.group(1))
                            subject = non_empty[1]
                            teacher = non_empty[2] if len(non_empty) >= 3 else ""
                            room = non_empty[3] if len(non_empty) >= 4 else ""
                            groups_data[current_group].append({
                                "number": num,
                                "name": subject,
                                "teacher": teacher,
                                "room": room
                            })
    except Exception as e:
        log(f"Ошибка парсинга DOCX {filepath}: {e}")
    
    for g, lessons in groups_data.items():
        unique = {}
        for l in lessons:
            unique[l['number']] = l
        groups_data[g] = sorted(unique.values(), key=lambda x: x['number'])
    
    return groups_data

def sync_all():
    log("="*60)
    log("СИНХРОНИЗАЦИЯ РАСПИСАНИЯ ДТК • ВСЕ ГРУППЫ")
    log("="*60)

    # Загружаем существующие данные
    all_groups_schedule = {}
    LOCAL_DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    if LOCAL_DATA_FILE.exists():
        try:
            with open(LOCAL_DATA_FILE, 'r', encoding='utf-8') as f:
                existing = json.load(f)
                all_groups_schedule = existing.get("groups", {})
        except Exception:
            pass

    # Сканируем диск
    root_items = get_disk_items()
    now = datetime.now()
    cur_month_ru = MONTH_NAMES_RU[now.month].lower()
    
    folders_to_check = []
    files_to_check = []
    
    for item in root_items:
        if item['type'] == 'dir':
            name_lower = item['name'].lower()
            if cur_month_ru in name_lower or (now.month > 1 and MONTH_NAMES_RU[now.month-1].lower() in name_lower):
                folders_to_check.append(item)
            elif "расписани" in name_lower or "2026" in name_lower:
                folders_to_check.append(item)
        elif item['type'] == 'file':
            files_to_check.append(item)

    for folder in folders_to_check:
        log(f"Папка: {folder['name']}")
        sub_items = get_disk_items(folder['path'])
        for sit in sub_items:
            if sit['type'] == 'file':
                files_to_check.append(sit)

    files_to_check.sort(key=lambda x: x.get('modified', ''), reverse=True)
    log(f"Всего файлов: {len(files_to_check)}. Обработка свежих...")

    with tempfile.TemporaryDirectory() as tmp_dir:
        processed = 0
        for f_item in files_to_check:
            fname = f_item['name']
            ext = Path(fname).suffix.lower()
            if ext not in ['.pdf', '.docx', '.xlsx']:
                continue
            fdate = parse_date_from_filename(fname)
            if not fdate:
                continue

            local_tmp = Path(tmp_dir) / fname
            log(f"Загрузка: {fname} ({fdate})...")
            if download_file(f_item['path'], local_tmp):
                file_groups = {}
                if ext == '.pdf':
                    file_groups = parse_pdf_all_groups(local_tmp)
                elif ext == '.docx':
                    file_groups = parse_docx_all_groups(local_tmp)

                for grp, lessons in file_groups.items():
                    if lessons:
                        if grp not in all_groups_schedule:
                            all_groups_schedule[grp] = {}
                        all_groups_schedule[grp][fdate] = lessons

                processed += 1
                if processed >= 5:
                    break

    # Формируем итоговый JSON
    # Для совместимости с виджетом сохраняем группу 20 в корень "schedule"
    group_20_sched = all_groups_schedule.get(DEFAULT_GROUP, {})
    
    payload = {
        "updated_at": datetime.now().astimezone().isoformat(),
        "default_group": DEFAULT_GROUP,
        "total_groups": len(all_groups_schedule),
        "groups_list": sorted(list(all_groups_schedule.keys())),
        "schedule": group_20_sched,
        "groups": all_groups_schedule
    }

    with open(LOCAL_DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    log(f"Сохранено {len(all_groups_schedule)} групп в {LOCAL_DATA_FILE}!")
    log(f"Список групп: {', '.join(sorted(list(all_groups_schedule.keys()))[:15])}...")

if __name__ == "__main__":
    sync_all()
