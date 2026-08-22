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

# ===================== КОНФИГУРАЦИЯ =====================
BOT_TOKEN = "8957553402:AAEZrm6gOIuwcHsqYzsxJ0Drpgw425Bwosw"  # Замените на реальный токен
ADMIN_IDS = [5078387190, 119715930]  # ID администраторов (Telegram user IDs)

# Цены на вирты
BUY_PRICE = 25   # Покупка у бота
SELL_PRICE = 20  # Продажа боту

# ===================== НАСТРОЙКА ЛОГИРОВАНИЯ =====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ===================== ИНИЦИАЛИЗАЦИЯ =====================
storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

# ===================== БАЗА ДАННЫХ =====================
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

# ===================== СОСТОЯНИЯ FSM =====================
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

# ===================== БЕЗОПАСНЫЙ ОТВЕТ НА CALLBACK =====================
async def safe_answer(callback: CallbackQuery, text: str = None, show_alert: bool = False):
    try:
        await callback.answer(text=text, show_alert=show_alert)
    except Exception as e:
        logger.warning(f"Не удалось ответить на callback (id={callback.id}): {e}")

# ===================== КЛАВИАТУРЫ =====================
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

# ===================== ВСПОМОГАТЕЛЬНЫЕ =====================
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

# ===================== ХЕНДЛЕРЫ ПОЛЬЗОВАТЕЛЯ =====================
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
    await state.clear()
    await state.set_state(OrderStates.choosing_type)
    await message.answer(
        "🔽 Выберите тип операции. Цена зависит от направления:",
        reply_markup=order_type_keyboard()
    )

@dp.callback_query(F.data == "new_order")
async def callback_new_order(callback: CallbackQuery, state: FSMContext):
    await safe_answer(callback)
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

# ===================== ХЕНДЛЕРЫ АДМИНА =====================
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

# ===================== ЗАПУСК =====================
async def main():
    init_db()
    logger.info("Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())