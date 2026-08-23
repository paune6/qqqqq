import asyncio
import logging
import sqlite3
from datetime import datetime
from typing import Optional, List, Dict, Any

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

BOT_TOKEN = "8643729424:AAEteJA_JHSm_H0T5r6UyJY3kI9hFhVUuxo"
ADMIN_IDS = [5078387190, 119715930]

BUY_PRICE = 25
SELL_PRICE = 20

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s"
)
logger = logging.getLogger(__name__)

storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

DB_NAME = "orders.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT,
            full_name TEXT,
            order_type TEXT NOT NULL,
            server TEXT NOT NULL,
            amount INTEGER NOT NULL,
            total_price INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            comment TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS banned_users (
            user_id INTEGER PRIMARY KEY,
            banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            reason TEXT
        )
    """)
    cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('maintenance', '0')")
    conn.commit()
    conn.close()
    logger.info("База данных инициализирована")

def db_execute(query: str, params: tuple = ()) -> List[tuple]:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(query, params)
    conn.commit()
    data = cur.fetchall()
    conn.close()
    return data

def create_order(user_id: int, username: str, full_name: str, order_type: str,
                 server: str, amount: int, total_price: int) -> int:
    query = """
        INSERT INTO orders (user_id, username, full_name, order_type, server, amount, total_price)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """
    params = (user_id, username, full_name, order_type, server, amount, total_price)
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(query, params)
    conn.commit()
    order_id = cur.lastrowid
    conn.close()
    logger.info(f"Создана заявка #{order_id} от пользователя {user_id}")
    return order_id

def get_order(order_id: int) -> Optional[Dict[str, Any]]:
    query = "SELECT * FROM orders WHERE id = ?"
    rows = db_execute(query, (order_id,))
    if not rows:
        return None
    row = rows[0]
    columns = ["id", "user_id", "username", "full_name", "order_type", "server",
               "amount", "total_price", "status", "comment", "created_at", "updated_at"]
    return dict(zip(columns, row))

def get_orders_by_user(user_id: int) -> List[Dict[str, Any]]:
    query = "SELECT * FROM orders WHERE user_id = ? ORDER BY created_at DESC"
    rows = db_execute(query, (user_id,))
    columns = ["id", "user_id", "username", "full_name", "order_type", "server",
               "amount", "total_price", "status", "comment", "created_at", "updated_at"]
    return [dict(zip(columns, row)) for row in rows]

def get_orders_by_status(status: str) -> List[Dict[str, Any]]:
    query = "SELECT * FROM orders WHERE status = ? ORDER BY created_at DESC"
    rows = db_execute(query, (status,))
    columns = ["id", "user_id", "username", "full_name", "order_type", "server",
               "amount", "total_price", "status", "comment", "created_at", "updated_at"]
    return [dict(zip(columns, row)) for row in rows]

def update_order_status(order_id: int, new_status: str, comment: str = ""):
    query = """
        UPDATE orders
        SET status = ?, comment = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """
    params = (new_status, comment, order_id)
    db_execute(query, params)
    logger.info(f"Заявка #{order_id} обновлена: статус={new_status}, комментарий='{comment}'")

def get_all_orders(limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
    query = "SELECT * FROM orders ORDER BY created_at DESC LIMIT ? OFFSET ?"
    rows = db_execute(query, (limit, offset))
    columns = ["id", "user_id", "username", "full_name", "order_type", "server",
               "amount", "total_price", "status", "comment", "created_at", "updated_at"]
    return [dict(zip(columns, row)) for row in rows]

def get_setting(key: str) -> Optional[str]:
    rows = db_execute("SELECT value FROM settings WHERE key = ?", (key,))
    return rows[0][0] if rows else None

def set_setting(key: str, value: str):
    db_execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))

def is_maintenance_on() -> bool:
    return get_setting("maintenance") == "1"

def toggle_maintenance(state: bool):
    set_setting("maintenance", "1" if state else "0")

def ban_user(user_id: int, reason: str = ""):
    db_execute("INSERT OR REPLACE INTO banned_users (user_id, reason) VALUES (?, ?)", (user_id, reason))

def unban_user(user_id: int):
    db_execute("DELETE FROM banned_users WHERE user_id = ?", (user_id,))

def is_user_banned(user_id: int) -> bool:
    rows = db_execute("SELECT 1 FROM banned_users WHERE user_id = ?", (user_id,))
    return bool(rows)

def get_banned_users() -> List[Dict[str, Any]]:
    rows = db_execute("SELECT user_id, banned_at, reason FROM banned_users ORDER BY banned_at DESC")
    return [{"user_id": r[0], "banned_at": r[1], "reason": r[2] or ""} for r in rows]

class OrderStates(StatesGroup):
    choosing_type = State()
    entering_server = State()
    entering_amount = State()
    confirming = State()

class AdminStates(StatesGroup):
    choosing_action = State()
    viewing_orders = State()
    viewing_order = State()
    entering_reject_reason = State()
    entering_message_to_user = State()

async def safe_answer(callback: CallbackQuery, text: str = None, show_alert: bool = False):
    try:
        await callback.answer(text=text, show_alert=show_alert)
    except Exception as e:
        logger.warning(f"Не удалось ответить на callback (id={callback.id}): {e}")

def main_menu_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📝 Новая заявка", callback_data="new_order"),
        InlineKeyboardButton(text="📋 Мои заявки", callback_data="my_orders")
    )
    builder.row(InlineKeyboardButton(text="❓ Помощь", callback_data="help"))
    return builder.as_markup()

def order_type_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🛒 Купить вирты (25 руб/шт)", callback_data="type_buy"),
        InlineKeyboardButton(text="💰 Продать вирты (20 руб/шт)", callback_data="type_sell")
    )
    builder.row(InlineKeyboardButton(text="🔙 Отмена", callback_data="cancel_order"))
    return builder.as_markup()

def confirm_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm_yes"),
        InlineKeyboardButton(text="❌ Отменить", callback_data="cancel_order")
    )
    return builder.as_markup()

def admin_main_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📋 Новые (pending)", callback_data="admin_pending"),
        InlineKeyboardButton(text="📌 В работе (accepted)", callback_data="admin_accepted")
    )
    builder.row(
        InlineKeyboardButton(text="✅ Завершённые", callback_data="admin_completed"),
        InlineKeyboardButton(text="❌ Отклонённые", callback_data="admin_rejected")
    )
    builder.row(
        InlineKeyboardButton(text="📊 Все заявки", callback_data="admin_all"),
        InlineKeyboardButton(text="🚪 Выйти", callback_data="admin_exit")
    )
    builder.row(
        InlineKeyboardButton(text="🔧 Техработы (вкл/выкл)", callback_data="admin_toggle_maintenance"),
        InlineKeyboardButton(text="❌ Отменить все pending", callback_data="admin_cancel_all")
    )
    return builder.as_markup()

def order_actions_keyboard(order_id: int, user_id: int = None):
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Принять", callback_data=f"admin_accept_{order_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"admin_reject_{order_id}")
    )
    builder.row(
        InlineKeyboardButton(text="✔️ Завершить", callback_data=f"admin_complete_{order_id}"),
        InlineKeyboardButton(text="✉️ Написать", callback_data=f"admin_msg_{order_id}")
    )
    if user_id:
        builder.row(
            InlineKeyboardButton(text="📋 Все заявки пользователя", callback_data=f"admin_user_orders_{user_id}")
        )
    builder.row(
        InlineKeyboardButton(text="🔙 Назад к списку", callback_data="admin_back_to_list")
    )
    return builder.as_markup()

def admin_back_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔙 В главное меню", callback_data="admin_main"))
    return builder.as_markup()

def cancel_keyboard(cancel_callback: str = "cancel_order"):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔙 Отмена", callback_data=cancel_callback))
    return builder.as_markup()

def user_orders_keyboard(orders: List[Dict[str, Any]], page: int = 0):
    builder = InlineKeyboardBuilder()
    start = page * 5
    end = start + 5
    page_orders = orders[start:end]
    for order in page_orders:
        status_emoji = {
            "pending": "⏳",
            "accepted": "📌",
            "rejected": "❌",
            "completed": "✅"
        }.get(order["status"], "❓")
        label = f"{status_emoji} Заявка #{order['id']} ({order['order_type']})"
        builder.row(InlineKeyboardButton(text=label, callback_data=f"user_view_{order['id']}"))
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"user_page_{page-1}"))
    if end < len(orders):
        nav_buttons.append(InlineKeyboardButton(text="Вперёд ➡️", callback_data=f"user_page_{page+1}"))
    if nav_buttons:
        builder.row(*nav_buttons)
    builder.row(InlineKeyboardButton(text="🔙 В главное меню", callback_data="main_menu"))
    return builder.as_markup()

def admin_orders_keyboard(orders: List[Dict[str, Any]], status_filter: str, page: int = 0):
    builder = InlineKeyboardBuilder()
    start = page * 5
    end = start + 5
    page_orders = orders[start:end]
    for order in page_orders:
        name = order['full_name'] or order['username'] or 'Без имени'
        username_str = f" (@{order['username']})" if order['username'] else " (без username)"
        label = f"#{order['id']} | {name}{username_str} | {order['order_type']} | {order['amount']} шт."
        builder.row(InlineKeyboardButton(text=label, callback_data=f"admin_view_{order['id']}"))
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"admin_page_{status_filter}_{page-1}"))
    if end < len(orders):
        nav_buttons.append(InlineKeyboardButton(text="Вперёд ➡️", callback_data=f"admin_page_{status_filter}_{page+1}"))
    if nav_buttons:
        builder.row(*nav_buttons)
    builder.row(InlineKeyboardButton(text="🔙 В главное меню", callback_data="admin_main"))
    return builder.as_markup()

def format_order_info(order: Dict[str, Any]) -> str:
    type_text = "Покупка" if order["order_type"] == "buy" else "Продажа"
    status_map = {
        "pending": "⏳ Ожидает обработки",
        "accepted": "📌 Принята (в работе)",
        "rejected": "❌ Отклонена",
        "completed": "✅ Завершена"
    }
    status_text = status_map.get(order["status"], order["status"])
    lines = [
        f"📋 Заявка #{order['id']}",
        f"👤 Имя: {order['full_name'] or 'не указано'}",
        f"🆔 Username: @{order['username'] if order['username'] else 'не указан'}",
        f"🆔 Telegram ID: {order['user_id']}",
        f"📌 Тип: {type_text}",
        f"🖥️ Сервер: {order['server']}",
        f"💰 Количество виртов: {order['amount']}",
        f"💵 Итоговая сумма: {order['total_price']} руб.",
        f"📅 Создана: {order['created_at']}",
        f"🔄 Статус: {status_text}",
    ]
    if order["comment"]:
        lines.append(f"💬 Комментарий: {order['comment']}")
    return "\n".join(lines)

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Я бот для обмена виртами на сервере **Black Russia**.\n\n"
        "📌 Цены:\n"
        "• Покупка виртов у бота – **25 руб/шт**\n"
        "• Продажа виртов боту – **20 руб/шт**\n\n"
        "🔹 Чтобы создать заявку, нажмите **«Новая заявка»**.\n"
        "🔹 Чтобы посмотреть свои заявки, нажмите **«Мои заявки»**.\n\n"
        "Если у вас возникнут вопросы, используйте команду /help.",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown"
    )

@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "📖 **Помощь по боту**\n\n"
        "🔹 `/new_order` – создать новую заявку на покупку или продажу виртов.\n"
        "🔹 `/my_orders` – просмотреть историю своих заявок.\n"
        "🔹 `/admin` – войти в админ-панель (только для администраторов).\n"
        "🔹 `/cancel` – отменить текущее действие.\n\n"
        "💡 Если бот не отвечает, попробуйте перезапустить его командой /start.",
        parse_mode="Markdown"
    )

@dp.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("✅ Действие отменено.", reply_markup=main_menu_keyboard())

@dp.message(Command("new_order"))
async def cmd_new_order(message: Message, state: FSMContext):
    if is_user_banned(message.from_user.id):
        await message.answer("⛔ Вы забанены и не можете создавать заявки.")
        return
    if is_maintenance_on():
        await message.answer("🔧 В настоящее время ведутся технические работы. Приём заявок временно закрыт.")
        return
    await state.clear()
    await state.set_state(OrderStates.choosing_type)
    await message.answer(
        "🔽 Выберите тип операции. Цена зависит от направления:",
        reply_markup=order_type_keyboard()
    )

@dp.callback_query(F.data == "new_order")
async def callback_new_order(callback: CallbackQuery, state: FSMContext):
    await safe_answer(callback)
    if is_user_banned(callback.from_user.id):
        await callback.message.edit_text("⛔ Вы забанены и не можете создавать заявки.", reply_markup=main_menu_keyboard())
        return
    if is_maintenance_on():
        await callback.message.edit_text("🔧 В настоящее время ведутся технические работы. Приём заявок временно закрыт.", reply_markup=main_menu_keyboard())
        return
    await cmd_new_order(callback.message, state)

@dp.callback_query(F.data == "my_orders")
async def callback_my_orders(callback: CallbackQuery):
    await safe_answer(callback)
    await show_my_orders(callback.message, callback.from_user.id, page=0)

@dp.callback_query(F.data.startswith("user_page_"))
async def callback_user_page(callback: CallbackQuery):
    page = int(callback.data.split("_")[2])
    await safe_answer(callback)
    await show_my_orders(callback.message, callback.from_user.id, page)

async def show_my_orders(message: Message, user_id: int, page: int):
    orders = get_orders_by_user(user_id)
    if not orders:
        await message.answer(
            "📭 У вас пока нет заявок. Создайте новую через кнопку «Новая заявка».",
            reply_markup=main_menu_keyboard()
        )
        return
    text = "📋 **Ваши заявки:**\n\n"
    start = page * 5
    end = start + 5
    for order in orders[start:end]:
        status_emoji = {
            "pending": "⏳",
            "accepted": "📌",
            "rejected": "❌",
            "completed": "✅"
        }.get(order["status"], "❓")
        type_text = "Покупка" if order["order_type"] == "buy" else "Продажа"
        text += f"{status_emoji} #{order['id']} – {type_text} – {order['amount']} шт. – {order['total_price']} руб.\n"
    if not orders[start:end]:
        text += "Нет заявок на этой странице."
    await message.answer(
        text,
        reply_markup=user_orders_keyboard(orders, page),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "main_menu")
async def callback_main_menu(callback: CallbackQuery, state: FSMContext):
    await safe_answer(callback)
    await state.clear()
    await callback.message.edit_text(
        "👋 Главное меню:",
        reply_markup=main_menu_keyboard()
    )

@dp.callback_query(F.data == "help")
async def callback_help(callback: CallbackQuery):
    await safe_answer(callback)
    await cmd_help(callback.message)

@dp.callback_query(F.data.startswith("type_"))
async def process_order_type(callback: CallbackQuery, state: FSMContext):
    await safe_answer(callback)
    order_type = callback.data.split("_")[1]
    await state.update_data(order_type=order_type)
    await state.set_state(OrderStates.entering_server)
    await callback.message.edit_text(
        "🖥️ Введите название игрового сервера (например, «Black Russia #1»):",
        reply_markup=cancel_keyboard()
    )

@dp.message(OrderStates.entering_server)
async def process_server(message: Message, state: FSMContext):
    server = message.text.strip()
    if not server:
        await message.answer("❌ Название сервера не может быть пустым. Попробуйте ещё раз:")
        return
    await state.update_data(server=server)
    await state.set_state(OrderStates.entering_amount)
    await message.answer(
        "🔢 Введите количество виртов (целое положительное число):",
        reply_markup=cancel_keyboard()
    )

@dp.message(OrderStates.entering_amount)
async def process_amount(message: Message, state: FSMContext):
    if not message.text.isdigit() or int(message.text) <= 0:
        await message.answer("❌ Пожалуйста, введите целое положительное число.")
        return
    amount = int(message.text)
    await state.update_data(amount=amount)
    data = await state.get_data()
    if data["order_type"] == "buy":
        total = amount * BUY_PRICE
        price_text = f"{BUY_PRICE} руб/шт"
    else:
        total = amount * SELL_PRICE
        price_text = f"{SELL_PRICE} руб/шт"
    await state.update_data(total_price=total)
    type_text = "покупку" if data["order_type"] == "buy" else "продажу"
    await state.set_state(OrderStates.confirming)
    await message.answer(
        f"📋 **Проверьте данные заявки:**\n\n"
        f"🔹 Операция: {type_text}\n"
        f"🖥️ Сервер: {data['server']}\n"
        f"🔢 Количество: {amount} виртов\n"
        f"💰 Цена за шт.: {price_text}\n"
        f"💵 Итоговая сумма: {total} руб.\n\n"
        f"✅ Если всё верно, подтвердите заявку.",
        reply_markup=confirm_keyboard(),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "cancel_order")
async def cancel_order(callback: CallbackQuery, state: FSMContext):
    await safe_answer(callback)
    await state.clear()
    await callback.message.edit_text(
        "❌ Действие отменено.",
        reply_markup=main_menu_keyboard()
    )

@dp.callback_query(F.data == "confirm_yes", StateFilter(OrderStates.confirming))
async def confirm_order(callback: CallbackQuery, state: FSMContext):
    await safe_answer(callback)
    data = await state.get_data()
    order_id = create_order(
        user_id=callback.from_user.id,
        username=callback.from_user.username or "",
        full_name=callback.from_user.full_name or "",
        order_type=data["order_type"],
        server=data["server"],
        amount=data["amount"],
        total_price=data["total_price"]
    )
    await state.clear()
    await callback.message.edit_text(
        f"✅ Заявка #{order_id} успешно создана!\n\n"
        f"Ожидайте ответа администратора. Статус заявки можно отслеживать в разделе «Мои заявки».",
        reply_markup=main_menu_keyboard()
    )
    username_str = f"@{callback.from_user.username}" if callback.from_user.username else "не указан"
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"🆕 **Новая заявка #{order_id}**\n\n"
                f"👤 Имя: {callback.from_user.full_name or 'не указано'}\n"
                f"🆔 Username: {username_str}\n"
                f"🆔 ID: {callback.from_user.id}\n"
                f"📌 Тип: {'Покупка' if data['order_type'] == 'buy' else 'Продажа'}\n"
                f"🖥️ Сервер: {data['server']}\n"
                f"🔢 Количество: {data['amount']}\n"
                f"💰 Сумма: {data['total_price']} руб.\n\n"
                f"Перейдите в админ-панель для обработки.",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление админу {admin_id}: {e}")

@dp.callback_query(F.data.startswith("user_view_"))
async def view_user_order(callback: CallbackQuery):
    order_id = int(callback.data.split("_")[2])
    order = get_order(order_id)
    if not order:
        await safe_answer(callback, "Заявка не найдена")
        return
    await safe_answer(callback)
    await callback.message.edit_text(
        format_order_info(order),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад к моим заявкам", callback_data="my_orders")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
            ]
        )
    )

@dp.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ Доступ запрещён. Вы не являетесь администратором.")
        return
    await state.clear()
    await state.set_state(AdminStates.choosing_action)
    await message.answer(
        "🏛 **Админ-панель**\n\n"
        "Выберите категорию заявок для просмотра:",
        reply_markup=admin_main_keyboard(),
        parse_mode="Markdown"
    )

@dp.message(Command("ahelp"))
async def cmd_ahelp(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    text = (
        "📖 **Список команд администратора**\n\n"
        "/ahelp - показать эту справку\n"
        "/ban @username [причина] - заблокировать пользователя\n"
        "/unban @username - разблокировать пользователя\n"
        "/maintenance on|off - включить/выключить техработы\n"
        "/cancel_all - отменить все заявки в статусе pending\n"
        "/admin - открыть админ-панель"
    )
    await message.answer(text, parse_mode="Markdown")

@dp.message(Command("ban"))
async def cmd_ban(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    args = message.text.split(maxsplit=2)
    if len(args) < 2:
        await message.answer("❌ Укажите пользователя: /ban @username [причина]")
        return
    username = args[1].lstrip("@")
    reason = args[2] if len(args) > 2 else "Без причины"
    try:
        user = await bot.get_chat(username)
        user_id = user.id
    except Exception:
        if args[1].isdigit():
            user_id = int(args[1])
        else:
            await message.answer("❌ Пользователь не найден. Укажите корректный @username или ID.")
            return
    ban_user(user_id, reason)
    await message.answer(f"✅ Пользователь {args[1]} забанен. Причина: {reason}")
    try:
        await bot.send_message(user_id, f"⛔ Вы были забанены администратором. Причина: {reason}")
    except:
        pass

@dp.message(Command("unban"))
async def cmd_unban(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    args = message.text.split()
    if len(args) < 2:
        await message.answer("❌ Укажите пользователя: /unban @username")
        return
    username = args[1].lstrip("@")
    try:
        user = await bot.get_chat(username)
        user_id = user.id
    except:
        if args[1].isdigit():
            user_id = int(args[1])
        else:
            await message.answer("❌ Пользователь не найден.")
            return
    unban_user(user_id)
    await message.answer(f"✅ Пользователь {args[1]} разбанен.")
    try:
        await bot.send_message(user_id, "✅ Вы были разбанены администратором.")
    except:
        pass

@dp.message(Command("maintenance"))
async def cmd_maintenance(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    args = message.text.split()
    if len(args) < 2 or args[1].lower() not in ("on", "off"):
        await message.answer("❌ Использование: /maintenance on|off")
        return
    state = args[1].lower() == "on"
    toggle_maintenance(state)
    status = "включён" if state else "выключен"
    await message.answer(f"🔧 Режим технических работ {status}.")

@dp.message(Command("cancel_all"))
async def cmd_cancel_all(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="✅ Да, отменить все", callback_data="cancel_all_confirm"),
        InlineKeyboardButton(text="❌ Нет", callback_data="cancel_all_cancel")
    )
    await message.answer(
        "⚠️ Вы уверены, что хотите отменить все заявки со статусом 'pending'?\nЭто действие необратимо.",
        reply_markup=kb.as_markup()
    )

@dp.callback_query(F.data == "cancel_all_confirm")
async def cancel_all_confirm(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await safe_answer(callback, "⛔ Нет прав")
        return
    await safe_answer(callback)
    pending = get_orders_by_status("pending")
    if not pending:
        await callback.message.edit_text("📭 Нет заявок в статусе pending.")
        return
    count = 0
    for order in pending:
        update_order_status(order["id"], "rejected", "Отменено администратором")
        try:
            await bot.send_message(
                order["user_id"],
                f"❌ Ваша заявка #{order['id']} была отменена администратором.\nПричина: массовая отмена."
            )
        except:
            pass
        count += 1
    await callback.message.edit_text(f"✅ Отменено {count} заявок.", reply_markup=admin_main_keyboard())

@dp.callback_query(F.data == "cancel_all_cancel")
async def cancel_all_cancel(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await safe_answer(callback, "⛔ Нет прав")
        return
    await safe_answer(callback)
    await callback.message.edit_text("❌ Отмена действия.", reply_markup=admin_main_keyboard())

@dp.callback_query(F.data == "admin_exit")
async def admin_exit(callback: CallbackQuery, state: FSMContext):
    await safe_answer(callback)
    await state.clear()
    await callback.message.edit_text(
        "🚪 Вы вышли из админ-панели.",
        reply_markup=main_menu_keyboard()
    )

@dp.callback_query(F.data == "admin_main")
async def admin_main(callback: CallbackQuery, state: FSMContext):
    await safe_answer(callback)
    await state.set_state(AdminStates.choosing_action)
    await callback.message.edit_text(
        "🏛 **Админ-панель**\n\nВыберите категорию:",
        reply_markup=admin_main_keyboard(),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data.startswith("admin_page_"))
async def admin_page_callback(callback: CallbackQuery):
    parts = callback.data.split("_")
    status_filter = parts[2]
    page = int(parts[3])
    await safe_answer(callback)
    await show_admin_orders(callback.message, status_filter, page)

async def show_admin_orders(message: Message, status_filter: str, page: int = 0):
    if status_filter == "all":
        orders = get_all_orders()
    else:
        orders = get_orders_by_status(status_filter)
    if not orders:
        await message.answer(
            "📭 В этой категории нет заявок.",
            reply_markup=admin_back_keyboard()
        )
        return
    start = page * 5
    end = start + 5
    text = f"📋 **Заявки ({status_filter})**\n\n"
    for order in orders[start:end]:
        type_text = "Покупка" if order["order_type"] == "buy" else "Продажа"
        name = order['full_name'] or order['username'] or 'Без имени'
        username_str = f" (@{order['username']})" if order['username'] else " (без username)"
        text += f"#{order['id']} | {name}{username_str} | {type_text} | {order['amount']} шт. | {order['total_price']} руб.\n"
    if not orders[start:end]:
        text += "Нет заявок на этой странице."
    await message.answer(
        text,
        reply_markup=admin_orders_keyboard(orders, status_filter, page),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data.startswith("admin_"))
async def admin_callback_router(callback: CallbackQuery, state: FSMContext):
    data = callback.data
    await safe_answer(callback)

    if data.startswith("admin_pending") or data.startswith("admin_accepted") or \
       data.startswith("admin_completed") or data.startswith("admin_rejected") or data.startswith("admin_all"):
        status_map = {
            "admin_pending": "pending",
            "admin_accepted": "accepted",
            "admin_completed": "completed",
            "admin_rejected": "rejected",
            "admin_all": "all"
        }
        status_filter = status_map.get(data, "pending")
        await show_admin_orders(callback.message, status_filter, page=0)
        return

    if data.startswith("admin_view_"):
        order_id = int(data.split("_")[2])
        await show_admin_order(callback.message, order_id)
        return

    if data.startswith("admin_accept_") or data.startswith("admin_reject_") or \
       data.startswith("admin_complete_") or data.startswith("admin_msg_"):
        parts = data.split("_")
        action = parts[1]
        order_id = int(parts[2])
        if action == "accept":
            update_order_status(order_id, "accepted")
            order = get_order(order_id)
            if order:
                await bot.send_message(
                    order["user_id"],
                    f"✅ Ваша заявка #{order_id} **принята** администратором!\n\n"
                    f"Ожидайте дальнейших инструкций. С вами свяжутся в ближайшее время.",
                    parse_mode="Markdown"
                )
            await callback.message.edit_text(
                f"✅ Заявка #{order_id} принята.",
                reply_markup=admin_main_keyboard()
            )
        elif action == "reject":
            await state.update_data(reject_order_id=order_id)
            await state.set_state(AdminStates.entering_reject_reason)
            await callback.message.edit_text(
                f"❌ Введите причину отклонения заявки #{order_id} (или отправьте '-' для пропуска):",
                reply_markup=cancel_keyboard(cancel_callback="admin_cancel_reject")
            )
        elif action == "complete":
            update_order_status(order_id, "completed")
            order = get_order(order_id)
            if order:
                await bot.send_message(
                    order["user_id"],
                    f"✔️ Ваша заявка #{order_id} **завершена**!\n\n"
                    f"Спасибо за сотрудничество! Если у вас остались вопросы, обратитесь к администратору.",
                    parse_mode="Markdown"
                )
            await callback.message.edit_text(
                f"✔️ Заявка #{order_id} завершена.",
                reply_markup=admin_main_keyboard()
            )
        elif action == "msg":
            await state.update_data(msg_order_id=order_id)
            await state.set_state(AdminStates.entering_message_to_user)
            await callback.message.edit_text(
                f"✉️ Введите текст сообщения, которое будет отправлено пользователю по заявке #{order_id}:",
                reply_markup=cancel_keyboard(cancel_callback="admin_cancel_msg")
            )
        return

    if data.startswith("admin_user_orders_"):
        user_id = int(data.split("_")[3])
        orders = get_orders_by_user(user_id)
        if not orders:
            await callback.message.edit_text(
                "📭 У этого пользователя нет заявок.",
                reply_markup=admin_back_keyboard()
            )
            return
        text = f"📋 **Заявки пользователя (ID {user_id})**\n\n"
        for order in orders[:10]:
            type_text = "Покупка" if order["order_type"] == "buy" else "Продажа"
            status_emoji = {
                "pending": "⏳",
                "accepted": "📌",
                "rejected": "❌",
                "completed": "✅"
            }.get(order["status"], "❓")
            text += f"{status_emoji} #{order['id']} – {type_text} – {order['amount']} шт. – {order['total_price']} руб.\n"
        kb = InlineKeyboardBuilder()
        kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back_to_list"))
        await callback.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="Markdown")
        return

    if data == "admin_back_to_list":
        await admin_main(callback, state)
        return

    if data == "admin_cancel_reject":
        await state.clear()
        await callback.message.edit_text(
            "❌ Отмена ввода причины.",
            reply_markup=admin_main_keyboard()
        )
        return

    if data == "admin_cancel_msg":
        await state.clear()
        await callback.message.edit_text(
            "❌ Отмена отправки сообщения.",
            reply_markup=admin_main_keyboard()
        )
        return

    if data == "admin_toggle_maintenance":
        if callback.from_user.id not in ADMIN_IDS:
            await safe_answer(callback, "⛔ Нет прав")
            return
        new_state = not is_maintenance_on()
        toggle_maintenance(new_state)
        status = "включён" if new_state else "выключен"
        await safe_answer(callback, f"Режим техработ {status}")
        await callback.message.edit_text(
            f"🔧 Техработы {status}.",
            reply_markup=admin_main_keyboard()
        )
        return

    if data == "admin_cancel_all":
        if callback.from_user.id not in ADMIN_IDS:
            await safe_answer(callback, "⛔ Нет прав")
            return
        await safe_answer(callback)
        kb = InlineKeyboardBuilder()
        kb.row(
            InlineKeyboardButton(text="✅ Да", callback_data="cancel_all_confirm"),
            InlineKeyboardButton(text="❌ Нет", callback_data="cancel_all_cancel")
        )
        await callback.message.edit_text(
            "⚠️ Вы уверены, что хотите отменить все pending заявки?",
            reply_markup=kb.as_markup()
        )
        return

    logger.warning(f"Неизвестный callback: {data}")

async def show_admin_order(message: Message, order_id: int):
    order = get_order(order_id)
    if not order:
        await message.answer("❌ Заявка не найдена.")
        return
    await message.answer(
        format_order_info(order),
        reply_markup=order_actions_keyboard(order_id, user_id=order["user_id"])
    )

@dp.message(AdminStates.entering_reject_reason)
async def process_reject_reason(message: Message, state: FSMContext):
    data = await state.get_data()
    order_id = data.get("reject_order_id")
    if not order_id:
        await state.clear()
        await message.answer("❌ Ошибка: заявка не найдена.", reply_markup=admin_main_keyboard())
        return
    reason = message.text.strip()
    if reason == "-":
        reason = ""
    update_order_status(order_id, "rejected", reason)
    order = get_order(order_id)
    if order:
        user_msg = f"❌ Ваша заявка #{order_id} **отклонена** администратором."
        if reason:
            user_msg += f"\n\nПричина: {reason}"
        await bot.send_message(order["user_id"], user_msg, parse_mode="Markdown")
    await state.clear()
    await message.answer(
        f"❌ Заявка #{order_id} отклонена. Причина сохранена.",
        reply_markup=admin_main_keyboard()
    )

@dp.message(AdminStates.entering_message_to_user)
async def process_admin_message_to_user(message: Message, state: FSMContext):
    data = await state.get_data()
    order_id = data.get("msg_order_id")
    if not order_id:
        await state.clear()
        await message.answer("❌ Ошибка: заявка не найдена.", reply_markup=admin_main_keyboard())
        return
    text = message.text.strip()
    if not text:
        await message.answer("❌ Сообщение не может быть пустым. Попробуйте снова.")
        return
    order = get_order(order_id)
    if order:
        try:
            await bot.send_message(
                order["user_id"],
                f"✉️ **Сообщение от администратора** (по заявке #{order_id}):\n\n{text}",
                parse_mode="Markdown"
            )
            await message.answer(f"✅ Сообщение отправлено пользователю по заявке #{order_id}.")
        except Exception as e:
            logger.error(f"Ошибка отправки сообщения пользователю {order['user_id']}: {e}")
            await message.answer(f"❌ Не удалось отправить сообщение: {e}")
    else:
        await message.answer("❌ Заявка не найдена.")
    await state.clear()
    await message.answer("Выберите действие:", reply_markup=admin_main_keyboard())

async def main():
    init_db()
    logger.info("Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
