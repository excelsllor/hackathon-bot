from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app.bots.preferences import (
    changed_preference_text,
    format_label,
    scope_label,
)
from app.bots.render import build_pages, group_by_region
from app.config import Settings
from app.db import Database
from app.models import User
from app.pipeline.classify import TopicsConfig
from app.pipeline.service import Pipeline
from app.services.digest import send_digest_now
from app.services.search import favorite_item, hide_item, search_items
from app.services.users import (
    get_or_create_user,
    get_subscriptions,
    is_admin,
    set_subscriptions,
    toggle_subscription,
)

logger = logging.getLogger(__name__)

MAIN_MENU = "🏠 Главное меню"
WEEKDAYS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


class TelegramBot:
    platform = "tg"

    def __init__(
        self,
        settings: Settings,
        db: Database,
        pipeline: Pipeline,
        topics: TopicsConfig,
    ) -> None:
        self.settings = settings
        self.db = db
        self.pipeline = pipeline
        self.topics = topics
        self.bot = Bot(token=settings.tg_token)
        self.dp = Dispatcher()
        self.router = Router()
        self.dp.include_router(self.router)
        self._last_request: dict[int, float] = {}
        self._background: set[asyncio.Task] = set()
        self._register()

    async def start(self) -> None:
        await self.bot.set_my_commands(
            [
                BotCommand(command="start", description="Запуск и меню"),
                BotCommand(command="find", description="Найти хакатоны"),
                BotCommand(command="subscriptions", description="Мои подписки"),
                BotCommand(command="settings", description="Настройки"),
                BotCommand(command="help", description="Помощь"),
            ]
        )
        await self.dp.start_polling(self.bot, handle_signals=False)

    async def close(self) -> None:
        for task in list(self._background):
            task.cancel()
        await self.bot.session.close()

    async def send_text(self, user_id: str, text: str) -> bool:
        try:
            await self.bot.send_message(
                chat_id=int(user_id),
                text=text,
                disable_web_page_preview=True,
            )
            return True
        except (TelegramForbiddenError, TelegramBadRequest, ValueError):
            logger.warning("telegram send failed for %s", user_id)
            return False

    def _register(self) -> None:
        r = self.router

        @r.message(Command("start"))
        async def start(message: Message) -> None:
            user = await self._user(message)
            await message.answer(
                "Привет! Я собираю хакатоны по программированию из RSS, HTML и API.\n\n"
                "Выбери темы и нажми «Показать». Ещё можно подписаться на ежедневный дайджест.",
                reply_markup=self._main_keyboard(),
                parse_mode=ParseMode.HTML,
            )
            logger.info("start user=%s", user.platform_user_id)

        @r.message(Command("find"))
        async def find(message: Message) -> None:
            await self._user(message)
            await message.answer(
                "Выбери темы:",
                reply_markup=self._topics_keyboard(self._mask_all(), "tf", "find"),
                parse_mode=ParseMode.HTML,
            )

        @r.message(Command("subscriptions"))
        async def subscriptions(message: Message) -> None:
            await self._send_subscriptions(message)

        @r.message(Command("settings"))
        async def settings_cmd(message: Message) -> None:
            user = await self._user(message)
            await message.answer(
                self._settings_text(user),
                reply_markup=self._settings_keyboard(user),
                parse_mode=ParseMode.HTML,
            )

        @r.message(Command("help"))
        async def help_cmd(message: Message) -> None:
            await message.answer(self._help_text(), parse_mode=ParseMode.HTML)

        @r.message(Command("stats"))
        async def stats_cmd(message: Message) -> None:
            user = await self._user(message)
            if not is_admin(user, self.settings):
                await message.answer("Команда только для админа.")
                return
            from app.services.search import stats as get_stats

            async with self.db.session() as session:
                counts = await get_stats(session)
                states = await self.pipeline.state_summary()
            lines = [
                f"📊 Элементов: {counts['items']} (активных {counts['active_items']})",
                f"👥 Пользователей: {counts['users']}",
                "",
                "Источники:",
            ]
            for state in states:
                status = state["status"] or "—"
                lines.append(
                    f"• {state['code']}: {status}, новых {state['last_new']}, "
                    f"ошибок подряд {state['failures']}"
                )
            await message.answer("\n".join(lines), parse_mode=ParseMode.HTML)

        @r.message(Command("refresh"))
        async def refresh_cmd(message: Message) -> None:
            user = await self._user(message)
            if not is_admin(user, self.settings):
                await message.answer("Команда только для админа.")
                return
            task = asyncio.create_task(self.pipeline.refresh_due_sources(force=True))
            self._track(task)
            await message.answer("Обновляю источники в фоне. Проверь /stats через пару минут.")

        @r.message(F.text.in_({"Найти", "найти", "/find"}))
        async def find_text(message: Message) -> None:
            await self._user(message)
            await message.answer(
                "Выбери темы:",
                reply_markup=self._topics_keyboard(self._mask_all(), "tf", "find"),
                parse_mode=ParseMode.HTML,
            )

        @r.message(F.text.in_({"Подписки", "подписки"}))
        async def subs_text(message: Message) -> None:
            await self._send_subscriptions(message)

        @r.message(F.text.in_({"Настройки", "настройки"}))
        async def settings_text(message: Message) -> None:
            user = await self._user(message)
            await message.answer(
                self._settings_text(user),
                reply_markup=self._settings_keyboard(user),
                parse_mode=ParseMode.HTML,
            )

        @r.callback_query(F.data)
        async def callback(call: CallbackQuery) -> None:
            try:
                await self._handle_callback(call)
            except Exception:
                logger.exception("telegram callback failed: %s", call.data)
                await call.answer("Ошибка. Попробуй ещё раз.", show_alert=False)

    async def _handle_callback(self, call: CallbackQuery) -> None:
        data = call.data or ""
        user = await self._user_from_callback(call)

        if data == "menu":
            await self._safe_edit(call, "Главное меню:", self._main_keyboard())
            await call.answer()
            return

        if data.startswith("menu:"):
            action = data.split(":", 1)[1]
            if action == "find":
                await self._safe_edit(
                    call,
                    "Выбери темы:",
                    self._topics_keyboard(self._mask_all(), "tf", "find"),
                )
            elif action == "subs":
                await self._edit_subscriptions(call, user)
            elif action == "settings":
                await self._safe_edit(
                    call,
                    self._settings_text(user),
                    self._settings_keyboard(user),
                )
            elif action == "help":
                await self._safe_edit(call, self._help_text(), self._main_keyboard())
            await call.answer()
            return

        if data.startswith("back:"):
            mask = int(data.split(":", 1)[1])
            await self._safe_edit(
                call,
                "Выбери темы:",
                self._topics_keyboard(mask, "tf", "find"),
            )
            await call.answer()
            return

        if data.startswith("tf:"):
            _, mask_raw, index_raw = data.split(":", 2)
            mask = self._toggle_mask(int(mask_raw), int(index_raw))
            await self._safe_edit(
                call,
                "Выбери темы:",
                self._topics_keyboard(mask, "tf", "find"),
            )
            await call.answer()
            return

        if data.startswith("tf_all:"):
            _, mask_raw = data.split(":", 1)
            await self._safe_edit(
                call,
                "Выбери темы:",
                self._topics_keyboard(self._mask_all(), "tf", "find"),
            )
            await call.answer()
            return

        if data.startswith("tf_none:"):
            await self._safe_edit(
                call,
                "Выбери темы:",
                self._topics_keyboard(0, "tf", "find"),
            )
            await call.answer()
            return

        if data.startswith("ts:"):
            _, mask_raw, index_raw = data.split(":", 2)
            index = int(index_raw)
            topic = self.topics.order[index]
            async with self.db.session() as session:
                user_db = await get_or_create_user(
                    session,
                    platform=user.platform,
                    platform_user_id=user.platform_user_id,
                    username=user.username,
                    settings=self.settings,
                )
                await toggle_subscription(session, user_db.id, topic)
                subs = await get_subscriptions(session, user_db.id)
            mask = self._mask_from_topics(subs)
            await self._edit_subscriptions(call, user, mask)
            await call.answer("Подписка обновлена")
            return

        if data.startswith("find:"):
            _, mask_raw, page_raw = data.split(":", 2)
            mask = int(mask_raw)
            topic_ids = self._mask_to_topics(mask)
            if not topic_ids:
                await call.answer("Выбери хотя бы одну тему", show_alert=True)
                return
            await call.answer("Ищу…")
            await self._run_search(call, user, topic_ids, mask, int(page_raw))
            return

        if data.startswith("all:"):
            _, mask_raw = data.split(":", 1)
            mask = int(mask_raw)
            topic_ids = self._mask_to_topics(mask)
            if not topic_ids:
                await call.answer("Выбери хотя бы одну тему", show_alert=True)
                return
            await call.answer("Собираю все результаты…")
            await self._show_all(call, user, topic_ids, mask)
            return

        if data.startswith("pg:"):
            _, mask_raw, page_raw = data.split(":", 2)
            mask = int(mask_raw)
            topic_ids = self._mask_to_topics(mask)
            await self._run_search(call, user, topic_ids, mask, int(page_raw), edit=True)
            await call.answer()
            return

        if data.startswith("hide:"):
            item_id = int(data.split(":", 1)[1])
            async with self.db.session() as session:
                await hide_item(session, user.id, item_id)
            await call.answer("Скрыто")
            return

        if data.startswith("fav:"):
            item_id = int(data.split(":", 1)[1])
            async with self.db.session() as session:
                await favorite_item(session, user.id, item_id)
            await call.answer("Добавлено в избранное")
            return

        if data in {"subs_all", "subs_none"}:
            async with self.db.session() as session:
                user_db = await get_or_create_user(
                    session,
                    platform=user.platform,
                    platform_user_id=user.platform_user_id,
                    username=user.username,
                    settings=self.settings,
                )
                topics = self.topics.order if data == "subs_all" else []
                await set_subscriptions(session, user_db.id, topics)
                subs = await get_subscriptions(session, user_db.id)
            await self._edit_subscriptions(call, user, self._mask_from_topics(subs))
            await call.answer("Подписки обновлены")
            return

        if data == "sched":
            await self._safe_edit(
                call,
                self._schedule_text(user),
                self._schedule_keyboard(user),
            )
            await call.answer()
            return

        if data.startswith("sd:"):
            _, mask_raw, index_raw = data.split(":", 2)
            mask = int(mask_raw) ^ (1 << int(index_raw))
            if mask == 0:
                await call.answer("Выбери хотя бы один день", show_alert=True)
                return
            await self._update_schedule(user, days=mask)
            fresh = await self._get_user(user)
            await self._safe_edit(
                call,
                self._schedule_text(fresh),
                self._schedule_keyboard(fresh),
            )
            await call.answer()
            return

        if data.startswith("sdset:"):
            mask = int(data.split(":", 1)[1])
            await self._update_schedule(user, days=mask)
            fresh = await self._get_user(user)
            await self._safe_edit(
                call,
                self._schedule_text(fresh),
                self._schedule_keyboard(fresh),
            )
            await call.answer("Сохранено")
            return

        if data.startswith("stime:"):
            mask = int(data.split(":", 1)[1])
            await self._safe_edit(
                call,
                "Выбери час дайджеста (МСК):",
                self._hours_keyboard(mask, user.digest_time),
            )
            await call.answer()
            return

        if data.startswith("sh:"):
            _, mask_raw, hour_raw = data.split(":", 2)
            mask = int(mask_raw)
            hour = int(hour_raw)
            await self._safe_edit(
                call,
                f"Выбери минуты для {hour:02d}:00 (МСК):",
                self._minutes_keyboard(mask, hour, user.digest_time),
            )
            await call.answer()
            return

        if data.startswith("sm:"):
            _, mask_raw, hour_raw, minute_raw = data.split(":", 3)
            mask = int(mask_raw)
            hour = int(hour_raw)
            minute = int(minute_raw)
            await self._update_schedule(
                user, days=mask, time_value=f"{hour:02d}:{minute:02d}"
            )
            fresh = await self._get_user(user)
            await self._safe_edit(
                call,
                self._schedule_text(fresh),
                self._schedule_keyboard(fresh),
            )
            await call.answer("Время сохранено")
            return

        if data == "digest_now":
            await call.answer("Проверяю…")
            status = await send_digest_now(
                self.db, self.settings, self.topics, self.send_text, user
            )
            if status == "no_topics":
                if call.message is not None:
                    await call.message.answer(
                        "Сначала выбери темы в разделе «Мои подписки»."
                    )
            elif status == "empty":
                if call.message is not None:
                    await call.message.answer(
                        "По выбранным темам сейчас нет активных хакатонов."
                    )
            elif status == "send_failed" and call.message is not None:
                await call.message.answer(
                    "Не удалось отправить дайджест. Проверь, что бот не заблокирован."
                )
            return

        if data.startswith("set:"):
            _, key, value = data.split(":", 2)
            await self._update_setting(user, key, value)
            fresh = await self._get_user(user)
            changed = key if key in {"format", "scope", "digest"} else None
            await self._safe_edit(
                call,
                self._settings_text(fresh, changed),
                self._settings_keyboard(fresh),
            )
            await call.answer(
                changed_preference_text(key, value) or "Сохранено"
            )
            return

        await call.answer()

    async def _refresh_stale(self) -> None:
        try:
            task = asyncio.create_task(
                self.pipeline.refresh_due_sources(
                    max_age_minutes=self.settings.on_demand_ttl_minutes
                )
            )
            self._track(task)
            await asyncio.wait({task}, timeout=self.settings.wait_for_refresh_seconds)
        except Exception:
            logger.exception("on-demand refresh failed")

    async def _run_search(
        self,
        call: CallbackQuery,
        user: User,
        topic_ids: list[str],
        mask: int,
        page: int,
        *,
        edit: bool = False,
    ) -> None:
        now = datetime.now(timezone.utc)
        await self._refresh_stale()

        async with self.db.session() as session:
            user_db = await get_or_create_user(
                session,
                platform=user.platform,
                platform_user_id=user.platform_user_id,
                username=user.username,
                settings=self.settings,
            )
            groups = await search_items(
                session,
                user_db,
                topic_ids,
                self.topics,
                limit=self.settings.max_items_per_search,
                now=now,
            )
        groups = group_by_region(groups)
        pages = build_pages(groups, self.topics, self.settings, html_mode=True)
        if not pages:
            text = (
                "Ничего не нашёл по выбранным темам и фильтрам.\n"
                "Попробуй выбрать больше тем или изменить настройки."
            )
            if edit:
                await self._safe_edit(call, text, self._main_keyboard())
            elif call.message is not None:
                await call.message.answer(text, reply_markup=self._main_keyboard())
            return
        page = max(0, min(page, len(pages) - 1))
        page_obj = pages[page]
        total_found = sum(len(items) for _, items in groups)
        header = f"Найдено: {total_found} · страница {page + 1}/{len(pages)}\n\n"
        text = header + page_obj.text
        keyboard = self._results_keyboard(page_obj.items, page, len(pages), mask)
        if edit:
            await self._safe_edit(call, text, keyboard)
        elif call.message is not None:
            await call.message.answer(
                text,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )

    async def _show_all(
        self,
        call: CallbackQuery,
        user: User,
        topic_ids: list[str],
        mask: int,
    ) -> None:
        now = datetime.now(timezone.utc)
        await self._refresh_stale()
        async with self.db.session() as session:
            user_db = await get_or_create_user(
                session,
                platform=user.platform,
                platform_user_id=user.platform_user_id,
                username=user.username,
                settings=self.settings,
            )
            groups = await search_items(
                session,
                user_db,
                topic_ids,
                self.topics,
                limit=self.settings.all_results_limit,
                now=now,
            )
        groups = group_by_region(groups)
        pages = build_pages(
            groups,
            self.topics,
            self.settings,
            html_mode=True,
            page_size=self.settings.all_results_page_size,
        )
        if not pages:
            text = (
                "Ничего не нашёл по выбранным темам и фильтрам.\n"
                "Попробуй выбрать больше тем или изменить настройки."
            )
            if call.message is not None:
                await call.message.answer(text, reply_markup=self._main_keyboard())
            return

        total = sum(len(items) for _, items in groups)
        chat_id = (
            call.message.chat.id if call.message is not None else call.from_user.id
        )
        max_messages = self.settings.all_results_max_messages
        shown = min(len(pages), max_messages)
        for index in range(shown):
            page_obj = pages[index]
            if index == 0:
                text = (
                    f"📜 Все результаты: {total} шт. по выбранным темам\n\n"
                    + page_obj.text
                )
            else:
                text = f"Страница {index + 1}/{len(pages)}\n\n" + page_obj.text
            keyboard = None
            if index == shown - 1:
                keyboard = self._main_keyboard()
            if index == 0 and call.message is not None:
                await call.message.answer(
                    text,
                    reply_markup=keyboard,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            else:
                await self.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_markup=keyboard,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            if index + 1 < shown:
                await asyncio.sleep(0.3)

        if len(pages) > shown:
            await self.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"Показаны первые {shown} сообщений из {len(pages)}. "
                    "Уточни тему или настройки, чтобы получить более короткий список."
                ),
                reply_markup=self._main_keyboard(),
            )

    async def _send_subscriptions(self, message: Message) -> None:
        user = await self._user(message)
        async with self.db.session() as session:
            subs = await get_subscriptions(session, user.id)
        mask = self._mask_from_topics(subs)
        await message.answer(
            self._subscriptions_text(user, subs),
            reply_markup=self._topics_keyboard(mask, "ts", "subs"),
            parse_mode=ParseMode.HTML,
        )

    async def _edit_subscriptions(self, call: CallbackQuery, user: User, mask: int | None = None) -> None:
        async with self.db.session() as session:
            subs = await get_subscriptions(session, user.id)
        if mask is None:
            mask = self._mask_from_topics(subs)
        await self._safe_edit(
            call,
            self._subscriptions_text(user, subs),
            self._topics_keyboard(mask, "ts", "subs"),
        )

    def _days_text(self, mask: int | None) -> str:
        value = 127 if mask is None else mask
        if value & 127 == 127:
            return "каждый день"
        if value == 31:
            return "будни"
        if value == 96:
            return "выходные"
        names = [WEEKDAYS[index] for index in range(7) if value & (1 << index)]
        return ", ".join(names) if names else "не выбраны"

    def _subscriptions_text(self, user: User, subs: list[str]) -> str:
        topics = ", ".join(self.topics.display(t) for t in subs) or "пока нет"
        if subs:
            digest = "включён" if user.digest_enabled else "выключен"
            digest_line = (
                f"Дайджест: {digest}, {self._days_text(user.digest_days)}, "
                f"{user.digest_time} МСК"
            )
        else:
            digest_line = "Дайджест не отправляется: сначала выбери хотя бы одну тему."
        return (
            f"🔔 <b>Темы дайджеста</b>\n\nТемы: {topics}\n"
            f"{digest_line}\n\n"
            "Нажимай темы, чтобы выбрать, что присылать в дайджесте."
        )

    def _settings_text(self, user: User, changed: str | None = None) -> str:
        fmt = format_label(user.format_pref)
        scope = scope_label(user.scope_pref)
        digest = "включён" if user.digest_enabled else "выключен"
        fmt_line = f"Формат: {fmt}"
        scope_line = f"География: {scope}"
        strict_line = "Строгая проверка РФ: включена"
        digest_line = f"Дайджест: {digest}"
        if changed == "format":
            fmt_line += " <b>(изменено)</b>"
        elif changed == "scope":
            scope_line += " <b>(изменено)</b>"
        elif changed == "digest":
            digest_line += " <b>(изменено)</b>"
        return (
            "⚙️ <b>Настройки</b>\n\n"
            f"{fmt_line}\n"
            f"{scope_line}\n"
            f"{strict_line}\n"
            f"{digest_line}\n"
            f"Расписание: {self._days_text(user.digest_days)}, {user.digest_time} МСК\n\n"
            "Строгая проверка РФ всегда включена: международные события без "
            "подтверждения участия из РФ скрываются."
        )

    def _schedule_text(self, user: User) -> str:
        return (
            "🗓 <b>Расписание дайджеста</b>\n\n"
            f"Дни: {self._days_text(user.digest_days)}\n"
            f"Время: {user.digest_time} МСК\n"
            f"Статус: {'включён' if user.digest_enabled else 'выключен'}\n\n"
            "Можно выбрать любые дни недели и любое время с шагом 5 минут."
        )

    def _schedule_keyboard(self, user: User) -> InlineKeyboardMarkup:
        mask = user.digest_days if user.digest_days is not None else 127
        rows: list[list[InlineKeyboardButton]] = []
        row: list[InlineKeyboardButton] = []
        for index, name in enumerate(WEEKDAYS):
            selected = bool(mask & (1 << index))
            row.append(
                InlineKeyboardButton(
                    text=f"{'✅' if selected else '⬜'} {name}",
                    callback_data=f"sd:{mask}:{index}",
                )
            )
            if len(row) == 4:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append(
            [
                InlineKeyboardButton(text="Будни", callback_data="sdset:31"),
                InlineKeyboardButton(text="Выходные", callback_data="sdset:96"),
                InlineKeyboardButton(text="Каждый день", callback_data="sdset:127"),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🕒 Время: {user.digest_time}",
                    callback_data=f"stime:{mask}",
                )
            ]
        )
        rows.append(
            [InlineKeyboardButton(text="📨 Отправить сейчас", callback_data="digest_now")]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="Назад к настройкам", callback_data="menu:settings"
                )
            ]
        )
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def _hours_keyboard(self, mask: int, current_time: str) -> InlineKeyboardMarkup:
        current_hour = int(current_time.split(":", 1)[0]) if ":" in current_time else 10
        rows: list[list[InlineKeyboardButton]] = []
        row: list[InlineKeyboardButton] = []
        for hour in range(24):
            mark = "✅ " if hour == current_hour else ""
            row.append(
                InlineKeyboardButton(
                    text=f"{mark}{hour:02d}",
                    callback_data=f"sh:{mask}:{hour}",
                )
            )
            if len(row) == 6:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([InlineKeyboardButton(text="Назад", callback_data="sched")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def _minutes_keyboard(
        self, mask: int, hour: int, current_time: str
    ) -> InlineKeyboardMarkup:
        if ":" in current_time:
            current_hour_raw, current_minute_raw = current_time.split(":", 1)
            current_hour = int(current_hour_raw)
            current_minute = int(current_minute_raw)
        else:
            current_hour, current_minute = 10, 0
        rows: list[list[InlineKeyboardButton]] = []
        row: list[InlineKeyboardButton] = []
        for minute in range(0, 60, 5):
            mark = "✅ " if minute == current_minute and hour == current_hour else ""
            row.append(
                InlineKeyboardButton(
                    text=f"{mark}{minute:02d}",
                    callback_data=f"sm:{mask}:{hour}:{minute}",
                )
            )
            if len(row) == 4:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append(
            [InlineKeyboardButton(text="Назад", callback_data=f"stime:{mask}")]
        )
        return InlineKeyboardMarkup(inline_keyboard=rows)

    async def _update_schedule(
        self, user: User, *, days: int | None = None, time_value: str | None = None
    ) -> None:
        async with self.db.session() as session:
            user_db = await session.get(User, user.id)
            if user_db is None:
                return
            if days is not None:
                user_db.digest_days = days
            if time_value is not None:
                user_db.digest_time = time_value
            user_db.last_digest_date = None
            user_db.last_digest_at = None

    def _help_text(self) -> str:
        return (
            "ℹ️ <b>Как пользоваться</b>\n\n"
            "• «Найти» — выбрать темы и получить свежие хакатоны.\n"
            "• «Темы дайджеста» — выбрать темы для рассылки.\n"
            "• «Настройки» — формат, география и дайджест.\n\n"
            "Источники: Devpost, Devfolio, Codenrock, ODS.ai, Telegram-каналы, "
            "ФСП и Школа 21.\n"
            "Международные события показываются только онлайн и с пометкой о проверке правил.\n"
            "Строгая проверка РФ всегда включена: международные события без подтверждения "
            "участия из РФ скрываются.\n"
            "Ссылки ведут на официальные страницы — всегда проверяй условия на сайте."
        )

    async def _update_setting(self, user: User, key: str, value: str) -> None:
        async with self.db.session() as session:
            user_db = await session.get(User, user.id)
            if user_db is None:
                return
            if key == "format" and value in {"any", "online", "offline"}:
                user_db.format_pref = value
            elif key == "scope" and value in {"both", "ru", "intl"}:
                user_db.scope_pref = value
            elif key == "digest":
                user_db.digest_enabled = value == "on"
            elif key == "time" and len(value) == 5 and ":" in value:
                user_db.digest_time = value

    async def _get_user(self, user: User) -> User:
        async with self.db.session() as session:
            fresh = await session.get(User, user.id)
            if fresh is None:
                return user
            session.expunge(fresh)
            return fresh

    async def _user(self, message: Message) -> User:
        from_user = message.from_user
        if from_user is None:
            raise RuntimeError("no from_user")
        async with self.db.session() as session:
            user = await get_or_create_user(
                session,
                platform=self.platform,
                platform_user_id=from_user.id,
                username=from_user.username,
                settings=self.settings,
            )
            session.expunge(user)
            return user

    async def _user_from_callback(self, call: CallbackQuery) -> User:
        from_user = call.from_user
        async with self.db.session() as session:
            user = await get_or_create_user(
                session,
                platform=self.platform,
                platform_user_id=from_user.id,
                username=from_user.username,
                settings=self.settings,
            )
            session.expunge(user)
            return user

    def _track(self, task: asyncio.Task) -> None:
        self._background.add(task)

        def done(completed: asyncio.Task) -> None:
            self._background.discard(completed)
            try:
                completed.result()
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("background task failed")

        task.add_done_callback(done)

    async def _safe_edit(
        self,
        call: CallbackQuery,
        text: str,
        keyboard: InlineKeyboardMarkup,
    ) -> None:
        if call.message is None:
            return
        try:
            await call.message.edit_text(
                text,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except TelegramBadRequest:
            try:
                await call.message.answer(
                    text,
                    reply_markup=keyboard,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            except TelegramBadRequest:
                logger.warning("edit and send failed")

    def _main_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔎 Найти хакатоны", callback_data="menu:find")],
                [InlineKeyboardButton(text="🔔 Темы дайджеста", callback_data="menu:subs")],
                [InlineKeyboardButton(text="⚙️ Настройки", callback_data="menu:settings")],
                [InlineKeyboardButton(text="ℹ️ Помощь", callback_data="menu:help")],
            ]
        )

    def _topics_keyboard(
        self, mask: int, action: str, kind: str
    ) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        row: list[InlineKeyboardButton] = []
        for index, topic in enumerate(self.topics.topics):
            selected = bool(mask & (1 << index))
            row.append(
                InlineKeyboardButton(
                    text=f"{'✅' if selected else '⬜'} {topic.emoji} {topic.title}",
                    callback_data=f"{action}:{mask}:{index}",
                )
            )
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        if kind == "find":
            rows.append(
                [
                    InlineKeyboardButton(text="🔎 Показать", callback_data=f"find:{mask}:0"),
                    InlineKeyboardButton(
                        text="📜 Все результаты", callback_data=f"all:{mask}"
                    ),
                ]
            )
            rows.append(
                [
                    InlineKeyboardButton(text="✅ Все темы", callback_data=f"tf_all:{mask}"),
                    InlineKeyboardButton(text="Сбросить", callback_data=f"tf_none:{mask}"),
                ]
            )
            rows.append(
                [
                    InlineKeyboardButton(
                        text="🔔 Темы дайджеста", callback_data="menu:subs"
                    )
                ]
            )
            rows.append(
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu")]
            )
        else:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="✅ Все темы", callback_data="subs_all"
                    ),
                    InlineKeyboardButton(
                        text="Очистить", callback_data="subs_none"
                    ),
                ]
            )
            rows.append(
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu")]
            )
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def _settings_keyboard(self, user: User) -> InlineKeyboardMarkup:
        def mark(active: bool) -> str:
            return "✅ " if active else ""

        rows = [
            [
                InlineKeyboardButton(
                    text=f"{mark(user.format_pref == 'any')}Любой формат",
                    callback_data="set:format:any",
                ),
                InlineKeyboardButton(
                    text=f"{mark(user.format_pref == 'online')}Онлайн",
                    callback_data="set:format:online",
                ),
                InlineKeyboardButton(
                    text=f"{mark(user.format_pref == 'offline')}Офлайн",
                    callback_data="set:format:offline",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=f"{mark(user.scope_pref == 'both')}РФ + мир",
                    callback_data="set:scope:both",
                ),
                InlineKeyboardButton(
                    text=f"{mark(user.scope_pref == 'ru')}РФ",
                    callback_data="set:scope:ru",
                ),
                InlineKeyboardButton(
                    text=f"{mark(user.scope_pref == 'intl')}Мир",
                    callback_data="set:scope:intl",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=(
                        f"{mark(user.digest_enabled)}Дайджест: "
                        f"{'включён' if user.digest_enabled else 'выключен'}"
                    ),
                    callback_data=f"set:digest:{'off' if user.digest_enabled else 'on'}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"📅 Расписание: {self._days_text(user.digest_days)}, {user.digest_time}",
                    callback_data="sched",
                )
            ],
            [InlineKeyboardButton(text=MAIN_MENU, callback_data="menu")],
        ]
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def _results_keyboard(
        self,
        items,
        page: int,
        pages_count: int,
        mask: int,
    ) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        for item in items:
            title = (item.title or "Открыть")[:48]
            rows.append(
                [
                    InlineKeyboardButton(text=f"🌐 {title}", url=item.url),
                    InlineKeyboardButton(text="🙈", callback_data=f"hide:{item.id}"),
                    InlineKeyboardButton(text="⭐", callback_data=f"fav:{item.id}"),
                ]
            )
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(
                InlineKeyboardButton(text="⬅️", callback_data=f"pg:{mask}:{page - 1}")
            )
        if page + 1 < pages_count:
            nav.append(
                InlineKeyboardButton(text="➡️", callback_data=f"pg:{mask}:{page + 1}")
            )
        rows.append(
            [InlineKeyboardButton(text="📜 Показать все", callback_data=f"all:{mask}")]
        )
        if nav:
            rows.append(nav)
        rows.append(
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"back:{mask}")]
        )
        rows.append([InlineKeyboardButton(text=MAIN_MENU, callback_data="menu")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def _mask_all(self) -> int:
        return (1 << len(self.topics.order)) - 1

    def _mask_from_topics(self, topics: list[str]) -> int:
        mask = 0
        for index, topic in enumerate(self.topics.order):
            if topic in topics:
                mask |= 1 << index
        return mask

    def _mask_to_topics(self, mask: int) -> list[str]:
        return [
            topic
            for index, topic in enumerate(self.topics.order)
            if mask & (1 << index)
        ]

    def _toggle_mask(self, mask: int, index: int) -> int:
        return mask ^ (1 << index)
