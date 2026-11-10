import os
import sys
import json
from datetime import datetime, timedelta
import asyncio
import requests

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

BOT_TOKEN = "8779227739:AAGMVUSqtfjyzLqpv3-J7uMDCGihs1BiThI"
SCHEDULE_URL = "https://raw.githubusercontent.com/yhdrfyh4-cmyk/dtk-schedule/main/data/schedule.json"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Хранилище выбранных групп пользователей {user_id: "20"}
user_groups = {}

DAY_NAMES = ["Воскресенье", "Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота"]

def get_bell_schedule(dow):
    if dow == 0:
        return []
    if dow == 1:
        return [
            {"num": 0, "name": "Разговоры о важном", "time": "8:30–9:00"},
            {"num": 1, "time": "9:10–10:40"},
            {"num": 2, "time": "11:00–12:30"},
            {"num": 3, "time": "12:50–14:20"},
            {"num": 4, "time": "14:30–16:00"},
        ]
    if dow == 6:
        return [
            {"num": 1, "time": "8:30–9:50"},
            {"num": 2, "time": "10:00–11:20"},
            {"num": 3, "time": "11:30–12:50"},
            {"num": 4, "time": "13:00–14:20"},
        ]
    return [
        {"num": 1, "time": "8:30–10:00"},
        {"num": 2, "time": "10:20–11:50"},
        {"num": 3, "time": "12:10–13:40"},
        {"num": 4, "time": "13:50–15:20"},
    ]

def fetch_schedule_data():
    try:
        r = requests.get(SCHEDULE_URL, timeout=8)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print("Ошибка загрузки:", e)
    return None

def format_day_schedule(target_date, group_name="20"):
    data = fetch_schedule_data()
    dow = target_date.weekday() + 1
    if dow == 7:
        dow = 0
    
    bells = get_bell_schedule(dow)
    if dow == 0:
        return "🎉 <b>Воскресенье — выходной день! Пар нет.</b>"
    
    d_str = target_date.strftime("%Y-%m-%d")
    md_str = target_date.strftime("%m-%d")
    
    lessons = None
    if data and "groups" in data and group_name in data["groups"]:
        grp_dict = data["groups"][group_name]
        if d_str in grp_dict:
            lessons = grp_dict[d_str]
        else:
            for k, v in grp_dict.items():
                if k.endswith(md_str):
                    lessons = v
                    break
    elif data and "schedule" in data and (group_name == "20" or group_name == "20 группа"):
        if d_str in data["schedule"]:
            lessons = data["schedule"][d_str]
        else:
            for k, v in data["schedule"].items():
                if k.endswith(md_str):
                    lessons = v
                    break

    day_name = DAY_NAMES[dow]
    header = f"📅 <b>{day_name}, {target_date.strftime('%d.%m')}</b> | 🎓 <b>Группа {group_name}</b>\n" + "—" * 28 + "\n\n"
    
    if not lessons and not bells:
        return header + "Занятий нет."
    
    if not lessons:
        return header + "Расписание для этой группы пока не выложено (или выходной)."

    lines = []
    for b in bells:
        num = b["num"]
        time_str = b["time"]
        name = b.get("name", "—")
        room = ""
        teacher = ""
        
        found = False
        for l in lessons:
            if l.get("number") == num:
                name = l.get("name", name)
                room = l.get("room", "")
                teacher = l.get("teacher", "")
                found = True
                break
        
        if not found and num == 0 and dow == 1:
            found = True
        
        if found:
            num_icon = "★" if num == 0 else f"{num}."
            room_str = f" [каб. {room}]" if room else ""
            teacher_str = f" ({teacher})" if teacher else ""
            lines.append(f"<b>{num_icon} {time_str}</b>\n└ <b>{name}</b>{room_str}{teacher_str}\n")
    
    if not lines:
        for l in lessons:
            lines.append(f"<b>Пара {l.get('number', '?')}:</b> {l.get('name', '—')} {l.get('room', '')}\n")
    
    return header + "\n".join(lines)

def get_main_keyboard(group="20"):
    kb = [
        [KeyboardButton(text="📅 На сегодня"), KeyboardButton(text="📆 На завтра")],
        [KeyboardButton(text="⏰ Звонки"), KeyboardButton(text=f"👥 Группа: {group}")],
        [KeyboardButton(text="🔄 Обновить")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    grp = user_groups.get(user_id, "20")
    welcome = (
        f"👋 Привет, {message.from_user.first_name}!\n\n"
        f"Я бот с расписанием колледжа ДТК для **всех групп** (сейчас выбрана: **{grp}**).\n"
        "Данные автоматически обновляются из облака.\n\n"
        "Чтобы сменить группу, просто отправь её номер в чат (например: `10`, `20`, `КСК-1`, `21`, `11`).\n"
        "Или выбери действие в меню 👇"
    )
    await message.answer(welcome, reply_markup=get_main_keyboard(grp), parse_mode="HTML")

@dp.message(F.text == "📅 На сегодня")
async def msg_today(message: types.Message):
    grp = user_groups.get(message.from_user.id, "20")
    now = datetime.now()
    text = format_day_schedule(now, grp)
    await message.answer(text, parse_mode="HTML")

@dp.message(F.text == "📆 На завтра")
async def msg_tomorrow(message: types.Message):
    grp = user_groups.get(message.from_user.id, "20")
    tomorrow = datetime.now() + timedelta(days=1)
    text = format_day_schedule(tomorrow, grp)
    await message.answer(text, parse_mode="HTML")

@dp.message(F.text == "⏰ Звонки")
async def msg_bells(message: types.Message):
    text = (
        "⏰ <b>РАСПИСАНИЕ ЗВОНКОВ ДТК</b>\n\n"
        "<b>📌 Понедельник:</b>\n"
        "0. Разговоры о важном: 8:30–9:00\n"
        "1 пара: 9:10–10:40\n"
        "2 пара: 11:00–12:30\n"
        "3 пара: 12:50–14:20\n"
        "4 пара: 14:30–16:00\n\n"
        "<b>📌 Вторник – Пятница:</b>\n"
        "1 пара: 8:30–10:00\n"
        "2 пара: 10:20–11:50\n"
        "3 пара: 12:10–13:40\n"
        "4 пара: 13:50–15:20\n\n"
        "<b>📌 Суббота:</b>\n"
        "1 пара: 8:30–9:50\n"
        "2 пара: 10:00–11:20\n"
        "3 пара: 11:30–12:50\n"
        "4 пара: 13:00–14:20"
    )
    await message.answer(text, parse_mode="HTML")

@dp.message(F.text.startswith("👥 Группа:"))
async def msg_change_group_hint(message: types.Message):
    await message.answer("Чтобы выбрать группу, просто напиши её номер в чат (например: `20`, `10`, `11`, `КСК-1`, `21`, `22.1`, `32` и т.д.)")

@dp.message(F.text == "🔄 Обновить")
async def msg_refresh(message: types.Message):
    data = fetch_schedule_data()
    total = data.get("total_groups", 0) if data else 0
    updated = data.get("updated_at", "недавно") if data else "нет данных"
    await message.answer(f"✅ Синхронизировано в облаке!\nВсего групп: {total}\nОбновлено: {updated[:19].replace('T', ' ')}")

# Перехват ввода номера группы
@dp.message()
async def msg_select_group(message: types.Message):
    user_input = message.text.strip()
    data = fetch_schedule_data()
    groups_list = data.get("groups_list", []) if data else []
    
    # Ищем совпадение
    matched = None
    for g in groups_list:
        if g.lower() == user_input.lower():
            matched = g
            break
    
    if not matched:
        # Попробуем без пробелов или точек
        clean_input = user_input.replace(" ", "").lower()
        for g in groups_list:
            if g.replace(" ", "").lower() == clean_input:
                matched = g
                break
    
    if matched:
        user_groups[message.from_user.id] = matched
        await message.answer(
            f"✅ Выбрана группа: <b>{matched}</b>!\nТеперь расписание будет показываться для неё.",
            reply_markup=get_main_keyboard(matched),
            parse_mode="HTML"
        )
    else:
        sample = ", ".join(groups_list[:12]) if groups_list else "10, 20, КСК-1, 21, 22.1"
        await message.answer(
            f"Не нашёл группу «{user_input}».\nДоступные группы (всего {len(groups_list)}): {sample}...",
            parse_mode="HTML"
        )

async def main():
    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
