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

GROUP = "20"
PUBLIC_KEY = "https://disk.yandex.ru/d/dixfX_wF6wcCmg"
YANDEX_API = "https://cloud-api.yandex.net/v1/disk/public/resources"
ONLINE_API_URL = "https://api.cl1p.net/dtk_makar_group20_schedule"
LOCAL_DATA_FILE = Path(__file__).parent.parent / "data" / "schedule.json"

MONTHS_RU = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
    "мая": 5, "июня": 6, "июля": 7, "августа": 8,
    "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
    "янв": 1, "фев": 2, "мар": 3, "апр": 4,
    "май": 5, "июн": 6, "июл": 7, "авг": 8,
    "сен": 9, "окт": 10, "ноя": 11, "дек": 12,
}

MONTH_NAMES_RU = [
    "", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"
]

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

def parse_date_from_filename(filename):
    """
    Извлекает дату из названий вроде:
    'Расписание 11.11.2026.pdf', 'Расписание 03.112026.pdf', 'Расписание 05.11..2026.pdf'
    """
    clean = re.sub(r'\s+', '', filename)
    # Поиск шаблона DD.MM.YYYY или DD.MMYYYY или DD.MM..YYYY
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

def parse_pdf_file(filepath, target_group=GROUP):
    """
    Парсит PDF расписание колледжа ДТК.
    Возвращает список занятий: [{'number': 1, 'name': '...', 'teacher': '...', 'room': '...'}]
    """
    lessons = []
    try:
        with pdfplumber.open(filepath) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                for table in tables:
                    in_target_group = False
                    for row in table:
                        if not row:
                            continue
                        first_cell = str(row[0] or '').strip()
                        # Группа может быть написана как "20", "20 группа", etc.
                        if first_cell == target_group:
                            in_target_group = True
                        elif first_cell != '' and in_target_group:
                            # Переход к следующей группе
                            in_target_group = False
                        
                        if in_target_group:
                            # Извлекаем номер пары
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
                            
                            # Предмет, преподаватель, кабинет
                            # В таблице строки: [Группа, №, Предмет, ?, ?, Преподаватель, Кабинет]
                            # Отфильтруем пустые ячейки после номера
                            cells = [str(c).replace('\n', ' ').strip() for c in row[1:] if c and str(c).strip()]
                            # Обычно: номер ('1.'), предмет ('География'), преподаватель ('Долгова М.Б.'), кабинет ('Чит.зал')
                            if len(cells) >= 2:
                                subject = cells[1]
                                teacher = cells[2] if len(cells) >= 3 else ""
                                room = cells[3] if len(cells) >= 4 else ""
                                
                                # Если кабинет попал не в ту ячейку
                                if not room and len(cells) >= 3 and re.search(r'\d+|зал|спорт', cells[-1], re.I):
                                    room = cells[-1]
                                    if teacher == room:
                                        teacher = ""
                                
                                if subject and not subject.isdigit():
                                    lessons.append({
                                        "number": lesson_num,
                                        "name": subject,
                                        "teacher": teacher,
                                        "room": room
                                    })
    except Exception as e:
        log(f"Ошибка парсинга PDF {filepath}: {e}")
    
    # Сортируем по номеру пары и убираем дубликаты
    unique = {}
    for l in lessons:
        unique[l['number']] = l
    return sorted(unique.values(), key=lambda x: x['number'])

def parse_docx_file(filepath, target_group=GROUP):
    """
    Парсит DOCX расписание колледжа ДТК.
    """
    if Document is None:
        return []
    lessons = []
    try:
        doc = Document(filepath)
        for table in doc.tables:
            in_target_group = False
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells]
                if not cells:
                    continue
                first_cell = cells[0]
                if first_cell == target_group:
                    in_target_group = True
                elif first_cell != '' and in_target_group:
                    in_target_group = False
                
                if in_target_group:
                    non_empty = [c for c in cells[1:] if c]
                    if len(non_empty) >= 2:
                        m = re.search(r'(\d+)', non_empty[0])
                        if m:
                            num = int(m.group(1))
                            subject = non_empty[1]
                            teacher = non_empty[2] if len(non_empty) >= 3 else ""
                            room = non_empty[3] if len(non_empty) >= 4 else ""
                            lessons.append({
                                "number": num,
                                "name": subject,
                                "teacher": teacher,
                                "room": room
                            })
    except Exception as e:
        log(f"Ошибка парсинга DOCX {filepath}: {e}")
    
    unique = {}
    for l in lessons:
        unique[l['number']] = l
    return sorted(unique.values(), key=lambda x: x['number'])

def fetch_all_schedules():
    """
    Скачивает и парсит все актуальные файлы расписания с Яндекс.Диска.
    """
    schedule_by_date = {}
    
    # Получаем корневой каталог диска
    root_items = get_disk_items()
    now = datetime.now()
    cur_month_ru = MONTH_NAMES_RU[now.month].lower()
    
    # Ищем папки месяцев (например "Ноябрь", "Октябрь")
    folders_to_check = []
    files_in_root = []
    
    for item in root_items:
        if item['type'] == 'dir':
            name_lower = item['name'].lower()
            # Проверяем текущий месяц и предыдущий
            if cur_month_ru in name_lower or (now.month > 1 and MONTH_NAMES_RU[now.month-1].lower() in name_lower):
                folders_to_check.append(item)
            elif "расписани" in name_lower or "2026" in name_lower:
                folders_to_check.append(item)
        elif item['type'] == 'file':
            files_in_root.append(item)
    
    all_files = list(files_in_root)
    for folder in folders_to_check:
        log(f"Сканирование папки: {folder['name']}")
        sub_items = get_disk_items(folder['path'])
        for sit in sub_items:
            if sit['type'] == 'file':
                all_files.append(sit)
    
    # Сортируем файлы по дате модификации (сначала самые новые)
    all_files.sort(key=lambda x: x.get('modified', ''), reverse=True)
    
    log(f"Всего найдено файлов: {len(all_files)}. Обработка последних файлов...")
    
    with tempfile.TemporaryDirectory() as tmp_dir:
        # Обрабатываем до 7 самых свежих файлов
        processed_count = 0
        for f_item in all_files:
            fname = f_item['name']
            ext = Path(fname).suffix.lower()
            if ext not in ['.pdf', '.docx', '.xlsx']:
                continue
            
            fdate = parse_date_from_filename(fname)
            if not fdate:
                continue
            
            # Если для этой даты уже есть расписание, пропускаем
            if fdate in schedule_by_date:
                continue
            
            local_tmp = Path(tmp_dir) / fname
            log(f"Загрузка: {fname} (дата: {fdate})...")
            if download_file(f_item['path'], local_tmp):
                lessons = []
                if ext == '.pdf':
                    lessons = parse_pdf_file(local_tmp, GROUP)
                elif ext == '.docx':
                    lessons = parse_docx_file(local_tmp, GROUP)
                
                if lessons:
                    log(f"  -> Найдено {len(lessons)} пар для группы {GROUP} на {fdate}!")
                    schedule_by_date[fdate] = lessons
                    for l in lessons:
                        log(f"     Пара {l['number']}: {l['name']} ({l.get('teacher', '')} ауд. {l.get('room', '')})")
                else:
                    log(f"  -> В файле {fname} нет пар для группы {GROUP} (возможно, выходной/практика)")
                
                processed_count += 1
                if processed_count >= 7:
                    break
    
    return schedule_by_date

def sync_and_publish():
    log("="*60)
    log(f"СИНХРОНИЗАЦИЯ РАСПИСАНИЯ ДТК • ГРУППА {GROUP}")
    log("="*60)
    
    # 1. Скачиваем актуальные расписания с Яндекс.Диска
    new_schedules = fetch_all_schedules()
    
    # 2. Загружаем существующее локальное расписание
    existing_data = {"group": GROUP, "schedule": {}}
    LOCAL_DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    if LOCAL_DATA_FILE.exists():
        try:
            with open(LOCAL_DATA_FILE, 'r', encoding='utf-8') as f:
                existing_data = json.load(f)
        except Exception:
            pass
    
    merged_schedule = existing_data.get("schedule", {})
    merged_schedule.update(new_schedules)
    
    # 3. Формируем итоговый JSON
    payload = {
        "updated_at": datetime.now().astimezone().isoformat(),
        "group": GROUP,
        "schedule": merged_schedule
    }
    
    # Сохраняем локально
    with open(LOCAL_DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    log(f"Локальный файл сохранен: {LOCAL_DATA_FILE}")
    
    # 4. Публикуем в онлайн хранилище dpaste
    try:
        # Дублируем даты для разных годов на случай рассинхрона дат на устройствах
        augmented = dict(merged_schedule)
        for date_key, lessons in list(merged_schedule.items()):
            parts = date_key.split('-')
            if len(parts) == 3:
                m_d = f"{parts[1]}-{parts[2]}"
                augmented[f"2024-{m_d}"] = lessons
                augmented[f"2025-{m_d}"] = lessons
                augmented[f"2026-{m_d}"] = lessons
        
        payload_aug = dict(payload)
        payload_aug['schedule'] = augmented
        json_str = json.dumps(payload_aug, ensure_ascii=False)
        
        r = requests.post("https://dpaste.org/api/", data={
            "content": json_str,
            "format": "url",
            "expiry_days": 365
        }, timeout=15)
        if r.status_code == 200:
            dp_url = r.text.strip() + "/raw"
            log(f"УСПЕШНО ОПУБЛИКОВАНО В ОНЛАЙН! Ссылка: {dp_url}")
    except Exception as e:
        log(f"Ошибка публикации в онлайн: {e}")

if __name__ == "__main__":
    sync_and_publish()
