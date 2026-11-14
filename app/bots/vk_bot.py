from __future__ import annotations

import asyncio
import json
import logging
import random
from datetime import datetime, timezone

from vkbottle import Callback, Keyboard, KeyboardButtonColor, OpenLink, Text
from vkbottle.bot import Bot, Message, MessageEvent
from vkbottle_types.events import GroupEventType

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
WEEKDAYS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


class VkBot:
    platform = "vk"

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
        self.bot = Bot(token=settings.vk_token)
        self._background: set[asyncio.Task] = set()
        self._last_message: dict[int, int] = {}
        self._answered_events: set[str] = set()
        self._register()

    async def start(self) -> None:
        await self.bot.run_polling()

    async def close(self) -> None:
        for task in list(self._background):
            task.cancel()

    async def send_text(self, user_id: str, text: str) -> bool:
        try:
            await self.bot.api.messages.send(
                peer_id=int(user_id),
                message=text,
                random_id=random.randint(1, 2_000_000_000),
                disable_mentions=1,
            )
            return True
        except Exception:
            logger.exception("vk send failed for %s", user_id)
            return False

    def _register(self) -> None:
        @self.bot.on.message()
        async def on_message(message: Message) -> None:
            text = (message.text or "").strip().lower()
            logger.info("vk message user=%s text=%r", message.from_id, text[:80])
            try:
                if text in {"начать", "start", "/start"}:
                    await self._send_main(message)
                elif text in {"найти", "/find"}:
                    await self._send_topics(message, self._mask_all())
                elif text in {"подписки", "темы дайджеста", "/subscriptions"}:
                    await self._send_subscriptions(message)
                elif text in {"настройки", "/settings"}:
                    await self._send_settings(message)
                elif text in {"помощь", "/help", "help"}:
                    await message.answer(
                        "Я собираю хакатоны по программированию.\n"
                        "«Найти» — выбрать темы, «Темы дайджеста» — выбор тем, "
                        "«Настройки» — формат, география и дайджест.\n"
                        "Строгая проверка РФ всегда включена: международные события "
                        "без подтверждения участия из РФ скрываются.",
                        keyboard=self._main_keyboard(),
                    )
                elif text in {"меню", "главное меню", "готово"}:
                    await self._send_main(message)
                elif text in {"myid", "/myid"}:
                    await message.answer(f"Твой VK ID: {message.from_id}")
                elif text in {"stats", "/stats"}:
                    await self._send_stats(message)
                else:
                    await message.answer(
                        "Я не понял команду. Напиши «Начать» или выбери «Найти».",
                        keyboard=self._main_keyboard(),
                    )
            except Exception:
                logger.exception("vk message handler failed for %s", message.from_id)
                try:
                    await message.answer(
                        "Ошибка обработки. Попробуй ещё раз.",
                        keyboard=self._main_keyboard(),
                    )
                except Exception:
                    logger.exception("vk fallback send failed")

        @self.bot.on.raw_event(GroupEventType.MESSAGE_EVENT, dataclass=MessageEvent)
        async def on_event(event: MessageEvent) -> None:
            try:
                await self._handle_event(event)
            except Exception:
                logger.exception("vk event failed: %s", getattr(event, "payload", None))
                await self._snackbar(event, "Ошибка. Попробуй ещё раз.")

    async def _handle_event(self, event: MessageEvent) -> None:
        payload = _payload(event)
        if not isinstance(payload, dict):
            await self._snackbar(event, "Непонятная команда")
            return
        cmd = payload.get("cmd")
        logger.info("vk event cmd=%s user=%s", cmd, event.user_id)
        ack_feedback = None
        if cmd == "set":
            ack_feedback = changed_preference_text(
                str(payload.get("key", "")), str(payload.get("value", ""))
            ) or "Сохранено"
        await self._ack_event(event, str(cmd or ""), feedback=ack_feedback)
        user = await self._user(event.user_id)

        if cmd == "menu":
            await self._send_to_event(event, "Главное меню:", self._main_keyboard())
            await self._snackbar(event, "")
            return
        if cmd == "menu_find":
            await self._send_to_event(
                event,
                "Выбери темы:",
                self._topics_keyboard(self._mask_all(), "tf"),
            )
            await self._snackbar(event, "")
            return
        if cmd == "menu_subs":
            async with self.db.session() as session:
                subs = await get_subscriptions(session, user.id)
            await self._send_to_event(
                event,
                self._subscriptions_text(user, subs),
                self._topics_keyboard(self._mask_from_topics(subs), "ts"),
                edit=True,
            )
            await self._snackbar(event, "")
            return
        if cmd == "menu_settings":
            await self._send_to_event(
                event,
                self._settings_text(user),
                self._settings_keyboard(user),
                edit=True,
            )
            await self._snackbar(event, "")
            return
        if cmd == "menu_help":
            await self._send_to_event(
                event,
                "Я собираю хакатоны по программированию.\n"
                "«Найти» — выбрать темы, «Темы дайджеста» — выбор тем, "
                "«Настройки» — формат, география и дайджест.\n"
                "Строгая проверка РФ всегда включена: международные события "
                "без подтверждения участия из РФ скрываются.",
                self._main_keyboard(),
            )
            await self._snackbar(event, "")
            return
        if cmd == "tf":
            mask = self._toggle_mask(int(payload.get("mask", 0)), int(payload.get("i", 0)))
            await self._send_to_event(
                event,
                "Выбери темы:",
                self._topics_keyboard(mask, "tf"),
            )
            await self._snackbar(event, "")
            return
        if cmd == "tf_all":
            await self._send_to_event(
                event,
                "Выбери темы:",
                self._topics_keyboard(self._mask_all(), "tf"),
            )
            await self._snackbar(event, "")
            return
        if cmd == "tf_none":
            await self._send_to_event(
                event,
                "Выбери темы:",
                self._topics_keyboard(0, "tf"),
            )
            await self._snackbar(event, "")
            return
        if cmd == "back":
            mask = int(payload.get("mask", 0))
            await self._send_to_event(
                event,
                "Выбери темы:",
                self._topics_keyboard(mask, "tf"),
            )
            await self._snackbar(event, "")
            return
        if cmd == "find":
            mask = int(payload.get("mask", 0))
            page = int(payload.get("page", 0))
            topic_ids = self._mask_to_topics(mask)
            if not topic_ids:
                await self._snackbar(event, "Выбери хотя бы одну тему")
                return
            await self._run_search(event, user, topic_ids, mask, page)
            await self._snackbar(event, "Ищу…")
            return
        if cmd == "pg":
            mask = int(payload.get("mask", 0))
            page = int(payload.get("page", 0))
            await self._run_search(
                event, user, self._mask_to_topics(mask), mask, page
            )
            return
        if cmd == "all":
            mask = int(payload.get("mask", 0))
            topic_ids = self._mask_to_topics(mask)
            if not topic_ids:
                await self._snackbar(event, "Выбери хотя бы одну тему")
                return
            await self._snackbar(event, "Собираю все результаты…")
            await self._show_all(event, user, topic_ids, mask)
            return
        if cmd == "hide":
            async with self.db.session() as session:
                await hide_item(session, user.id, int(payload.get("id", 0)))
            await self._snackbar(event, "Скрыто")
            return
        if cmd == "fav":
            async with self.db.session() as session:
                await favorite_item(session, user.id, int(payload.get("id", 0)))
            await self._snackbar(event, "В избранном")
            return
        if cmd == "set":
            key = str(payload.get("key", ""))
            value = str(payload.get("value", ""))
            await self._update_setting(user, key, value)
            fresh = await self._get_user(user)
            changed = key if key in {"format", "scope", "digest"} else None
            await self._send_to_event(
                event,
                self._settings_text(fresh, changed),
                self._settings_keyboard(fresh),
            )
            await self._snackbar(
                event, changed_preference_text(key, value) or "Сохранено"
            )
            return
        if cmd == "sched":
            await self._send_to_event(
                event,
                self._schedule_text(user),
                self._schedule_keyboard(user),
                edit=True,
            )
            await self._snackbar(event, "")
            return
        if cmd == "sd":
            current = user.digest_days if user.digest_days is not None else 127
            mask = current ^ (1 << int(payload.get("i", 0)))
            if mask == 0:
                await self._snackbar(event, "Выбери хотя бы один день")
                return
            await self._update_schedule(user, days=mask)
            fresh = await self._get_user(user)
            await self._send_to_event(
                event,
                self._schedule_text(fresh),
                self._schedule_keyboard(fresh),
                edit=True,
            )
            await self._snackbar(event, "")
            return
        if cmd == "sdset":
            mask = int(payload.get("mask", 127))
            await self._update_schedule(user, days=mask)
            fresh = await self._get_user(user)
            await self._send_to_event(
                event,
                self._schedule_text(fresh),
                self._schedule_keyboard(fresh),
            )
            await self._snackbar(event, "Сохранено")
            return
        if cmd == "stime":
            await self._send_to_event(
                event,
                "Выбери час дайджеста (МСК):",
                self._hours_keyboard(
                    user.digest_days if user.digest_days is not None else 127,
                    user.digest_time,
                ),
                edit=True,
            )
            await self._snackbar(event, "")
            return
        if cmd == "sh":
            mask = int(payload.get("mask", 127))
            hour = int(payload.get("hour", 10))
            await self._send_to_event(
                event,
                f"Выбери минуты для {hour:02d}:00 (МСК):",
                self._minutes_keyboard(mask, hour, user.digest_time),
            )
            await self._snackbar(event, "")
            return
        if cmd == "sm":
            mask = int(payload.get("mask", 127))
            hour = int(payload.get("hour", 10))
            minute = int(payload.get("minute", 0))
            await self._update_schedule(
                user, days=mask, time_value=f"{hour:02d}:{minute:02d}"
            )
            fresh = await self._get_user(user)
            await self._send_to_event(
                event,
                self._schedule_text(fresh),
                self._schedule_keyboard(fresh),
            )
            await self._snackbar(event, "Время сохранено")
            return
        if cmd == "digest_now":
            status = await send_digest_now(
                self.db, self.settings, self.topics, self.send_text, user
            )
            if status == "no_topics":
                await self._send_to_event(
                    event,
                    "Сначала выбери темы в разделе «Подписки».",
                    self._main_keyboard(),
                )
            elif status == "empty":
                await self._send_to_event(
                    event,
                    "По выбранным темам сейчас нет активных хакатонов.",
                    self._main_keyboard(),
                )
            elif status == "send_failed":
                await self._snackbar(event, "Не удалось отправить дайджест")
            else:
                await self._snackbar(event, "Дайджест отправлен")
            return
        if cmd in {"subs_all", "subs_none"}:
            async with self.db.session() as session:
                user_db = await get_or_create_user(
                    session,
                    platform=self.platform,
                    platform_user_id=user.platform_user_id,
                    username=user.username,
                    settings=self.settings,
                )
                topics = self.topics.order if cmd == "subs_all" else []
                await set_subscriptions(session, user_db.id, topics)
                subs = await get_subscriptions(session, user_db.id)
            await self._send_to_event(
                event,
                self._subscriptions_text(user, subs),
                self._topics_keyboard(self._mask_from_topics(subs), "ts"),
            )
            await self._snackbar(event, "Подписки обновлены")
            return
        if cmd == "ts":
            topic = self.topics.order[int(payload.get("i", 0))]
            async with self.db.session() as session:
                await toggle_subscription(session, user.id, topic)
                subs = await get_subscriptions(session, user.id)
            await self._send_to_event(
                event,
                self._subscriptions_text(user, subs),
                self._topics_keyboard(self._mask_from_topics(subs), "ts"),
            )
            await self._snackbar(event, "Подписка обновлена")
            return
        await self._snackbar(event, "")

    async def _refresh_stale(self) -> None:
        try:
            task = asyncio.create_task(
                self.pipeline.refresh_due_sources(
                    max_age_minutes=self.settings.on_demand_ttl_minutes
                )
            )
            self._background.add(task)
            task.add_done_callback(self._background.discard)
            await asyncio.wait({task}, timeout=self.settings.wait_for_refresh_seconds)
        except Exception:
            logger.exception("vk on-demand refresh failed")

    async def _run_search(
        self,
        event: MessageEvent,
        user: User,
        topic_ids: list[str],
        mask: int,
        page: int,
    ) -> None:
        now = datetime.now(timezone.utc)
        await self._refresh_stale()

        async with self.db.session() as session:
            user_db = await get_or_create_user(
                session,
                platform=self.platform,
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
        pages = build_pages(groups, self.topics, self.settings, html_mode=False)
        if not pages:
            await self._send_to_event(
                event,
                "Ничего не нашёл. Попробуй выбрать больше тем или изменить настройки.",
                self._main_keyboard(),
            )
            return
        page = max(0, min(page, len(pages) - 1))
        page_obj = pages[page]
        total_found = sum(len(items) for _, items in groups)
        header = f"Найдено: {total_found} · страница {page + 1}/{len(pages)}\n\n"
        await self._send_to_event(
            event,
            header + page_obj.text,
            self._results_keyboard(page_obj.items, page, len(pages), mask),
        )

    async def _show_all(
        self,
        event: MessageEvent,
        user: User,
        topic_ids: list[str],
        mask: int,
    ) -> None:
        now = datetime.now(timezone.utc)
        await self._refresh_stale()
        async with self.db.session() as session:
            user_db = await get_or_create_user(
                session,
                platform=self.platform,
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
            html_mode=False,
            page_size=self.settings.all_results_page_size,
        )
        if not pages:
            await self._send_to_event(
                event,
                "Ничего не нашёл. Попробуй выбрать больше тем или изменить настройки.",
                self._main_keyboard(),
            )
            return
        total = sum(len(items) for _, items in groups)
        shown = min(len(pages), self.settings.vk_all_results_max_messages)
        for index in range(shown):
            page_obj = pages[index]
            if index == 0:
                text = f"📜 Все результаты: {total} шт. по выбранным темам\n\n" + page_obj.text
            else:
                text = f"Страница {index + 1}/{len(pages)}\n\n" + page_obj.text
            keyboard = self._main_keyboard() if index == shown - 1 else None
            await self._send_to_event(
                event, text, keyboard, edit=(index == 0)
            )
            if index + 1 < shown:
                await asyncio.sleep(self.settings.vk_all_results_delay_seconds)
        if len(pages) > shown:
            await self._send_to_event(
                event,
                f"Показаны первые {shown} сообщений из {len(pages)}. "
                "Уточни тему или настройки, чтобы сократить список.",
                self._main_keyboard(),
            )

    async def _send_ui(
        self, peer_id: int, text: str, keyboard: str | None = None
    ) -> None:
        result = await self.bot.api.messages.send(
            peer_id=int(peer_id),
            message=text[:4090],
            random_id=random.randint(1, 2_000_000_000),
            keyboard=keyboard,
            disable_mentions=1,
        )
        self._remember_message(peer_id, result)

    def _remember_message(self, peer_id: int, result) -> None:
        value: int | None = None
        if isinstance(result, int):
            value = result
        elif isinstance(result, list) and result:
            item = result[0]
            value = getattr(item, "message_id", None) or getattr(
                item, "conversation_message_id", None
            )
        else:
            value = getattr(result, "message_id", None) or getattr(
                result, "conversation_message_id", None
            )
        if value:
            self._last_message[int(peer_id)] = int(value)

    async def _show_ui(
        self, peer_id: int, text: str, keyboard: str | None = None
    ) -> None:
        message_id = self._last_message.get(int(peer_id))
        if message_id:
            try:
                await self.bot.api.messages.edit(
                    peer_id=int(peer_id),
                    message_id=message_id,
                    message=text[:4090],
                    keyboard=keyboard,
                )
                return
            except Exception:
                logger.debug("vk ui edit failed; sending a new screen", exc_info=True)
        await self._send_ui(peer_id, text, keyboard)

    async def _send_main(self, message: Message) -> None:
        await self._show_ui(
            message.peer_id,
            "Привет! Я собираю хакатоны по программированию.\n"
            "Выбери «Найти», чтобы отфильтровать по темам.",
            self._main_keyboard(),
        )

    async def _send_topics(self, message: Message, mask: int) -> None:
        await self._show_ui(
            message.peer_id,
            "Выбери темы:",
            self._topics_keyboard(mask, "tf"),
        )

    async def _send_subscriptions(self, message: Message) -> None:
        user = await self._user(message.from_id)
        async with self.db.session() as session:
            subs = await get_subscriptions(session, user.id)
        await self._show_ui(
            message.peer_id,
            self._subscriptions_text(user, subs),
            self._topics_keyboard(self._mask_from_topics(subs), "ts"),
        )

    async def _send_settings(self, message: Message) -> None:
        user = await self._user(message.from_id)
        await self._show_ui(
            message.peer_id,
            self._settings_text(user),
            self._settings_keyboard(user),
        )

    async def _send_stats(self, message: Message) -> None:
        user = await self._user(message.from_id)
        if not is_admin(user, self.settings):
            await message.answer("Команда только для админа.")
            return
        from app.services.search import stats as get_stats

        async with self.db.session() as session:
            counts = await get_stats(session)
        await self._send_ui(
            message.peer_id,
            f"Элементов: {counts['items']} (активных {counts['active_items']})\n"
            f"Пользователей: {counts['users']}",
        )

    async def _user(self, user_id: int) -> User:
        async with self.db.session() as session:
            user = await get_or_create_user(
                session,
                platform=self.platform,
                platform_user_id=user_id,
                username=None,
                settings=self.settings,
            )
            session.expunge(user)
            return user

    async def _get_user(self, user: User) -> User:
        async with self.db.session() as session:
            fresh = await session.get(User, user.id)
            if fresh is None:
                return user
            session.expunge(fresh)
            return fresh

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
            elif key == "time":
                user_db.digest_time = value

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
            f"Темы дайджеста\n\nТемы: {topics}\n"
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
            fmt_line += " (изменено)"
        elif changed == "scope":
            scope_line += " (изменено)"
        elif changed == "digest":
            digest_line += " (изменено)"
        return (
            "Настройки\n\n"
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
            "Расписание дайджеста\n\n"
            f"Дни: {self._days_text(user.digest_days)}\n"
            f"Время: {user.digest_time} МСК\n"
            f"Статус: {'включён' if user.digest_enabled else 'выключен'}\n\n"
            "Можно выбрать любые дни недели и любое время с шагом 5 минут."
        )

    def _schedule_keyboard(self, user: User) -> str:
        mask = user.digest_days if user.digest_days is not None else 127
        keyboard = Keyboard(one_time=False, inline=False)
        for index, name in enumerate(WEEKDAYS):
            selected = bool(mask & (1 << index))
            keyboard.add(
                Callback(
                    f"{'✅' if selected else '⬜'} {name}",
                    payload={"cmd": "sd", "i": index},
                )
            )
            if (index + 1) % 4 == 0:
                keyboard.row()
        keyboard.row()
        keyboard.add(Callback("Будни", payload={"cmd": "sdset", "mask": 31}))
        keyboard.add(Callback("Выходные", payload={"cmd": "sdset", "mask": 96}))
        keyboard.add(Callback("Каждый день", payload={"cmd": "sdset", "mask": 127}))
        keyboard.row()
        keyboard.add(
            Callback("Время", payload={"cmd": "stime", "mask": mask})
        )
        keyboard.add(Callback("Отправить сейчас", payload={"cmd": "digest_now"}))
        keyboard.add(Text("Меню"))
        return keyboard.get_json()

    def _hours_keyboard(self, mask: int, current_time: str) -> str:
        current_hour = int(current_time.split(":", 1)[0]) if ":" in current_time else 10
        keyboard = Keyboard(one_time=False, inline=False)
        for hour in range(25):
            if hour == 24:
                keyboard.add(Callback("Назад", payload={"cmd": "sched"}))
                break
            mark = "✅ " if hour == current_hour else ""
            keyboard.add(
                Callback(
                    f"{mark}{hour:02d}",
                    payload={"cmd": "sh", "mask": mask, "hour": hour},
                )
            )
            if (hour + 1) % 5 == 0:
                keyboard.row()
        return keyboard.get_json()

    def _minutes_keyboard(self, mask: int, hour: int, current_time: str) -> str:
        if ":" in current_time:
            current_hour_raw, current_minute_raw = current_time.split(":", 1)
            current_hour = int(current_hour_raw)
            current_minute = int(current_minute_raw)
        else:
            current_hour, current_minute = 10, 0
        keyboard = Keyboard(one_time=False, inline=False)
        for index, minute in enumerate(range(0, 60, 5)):
            mark = "✅ " if minute == current_minute and hour == current_hour else ""
            keyboard.add(
                Callback(
                    f"{mark}{minute:02d}",
                    payload={
                        "cmd": "sm",
                        "mask": mask,
                        "hour": hour,
                        "minute": minute,
                    },
                )
            )
            if (index + 1) % 5 == 0:
                keyboard.row()
        keyboard.add(Callback("Назад", payload={"cmd": "stime", "mask": mask}))
        return keyboard.get_json()

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

    def _main_keyboard(self) -> str:
        keyboard = Keyboard(one_time=False, inline=False)
        keyboard.add(Text("Начать"), color=KeyboardButtonColor.PRIMARY)
        keyboard.row()
        keyboard.add(Text("Найти"))
        keyboard.add(Text("Темы дайджеста"))
        keyboard.row()
        keyboard.add(Text("Настройки"))
        keyboard.add(Text("Помощь"))
        return keyboard.get_json()

    def _topics_keyboard(self, mask: int, action: str) -> str:
        keyboard = Keyboard(one_time=False, inline=False)
        for index, topic in enumerate(self.topics.topics):
            selected = bool(mask & (1 << index))
            label = f"{'✅' if selected else '⬜'} {topic.emoji} {topic.title}"[:40]
            keyboard.add(
                Callback(label, payload={"cmd": action, "mask": mask, "i": index})
            )
            if (index + 1) % 4 == 0:
                keyboard.row()
        keyboard.row()
        if action == "tf":
            keyboard.add(
                Callback("🔎 Показать", payload={"cmd": "find", "mask": mask, "page": 0}),
                color=KeyboardButtonColor.POSITIVE,
            )
            keyboard.add(Callback("📜 Все", payload={"cmd": "all", "mask": mask}))
            keyboard.add(Callback("Все темы", payload={"cmd": "tf_all", "mask": mask}))
            keyboard.add(Callback("Сброс", payload={"cmd": "tf_none", "mask": mask}))
            keyboard.row()
            keyboard.add(
                Callback("Темы дайджеста", payload={"cmd": "menu_subs"})
            )
            keyboard.add(Callback("⬅️ Назад", payload={"cmd": "menu"}))
        else:
            keyboard.add(Callback("Все темы", payload={"cmd": "subs_all"}))
            keyboard.add(Callback("Очистить", payload={"cmd": "subs_none"}))
            keyboard.add(Callback("⬅️ Назад", payload={"cmd": "menu"}))
        return keyboard.get_json()

    def _settings_keyboard(self, user: User) -> str:
        def mark(active: bool) -> str:
            return "✅ " if active else ""

        keyboard = Keyboard(one_time=False, inline=False)
        keyboard.add(
            Callback(
                f"{mark(user.format_pref == 'any')}Любой формат",
                payload={"cmd": "set", "key": "format", "value": "any"},
            )
        )
        keyboard.add(
            Callback(
                f"{mark(user.format_pref == 'online')}Онлайн",
                payload={"cmd": "set", "key": "format", "value": "online"},
            )
        )
        keyboard.add(
            Callback(
                f"{mark(user.format_pref == 'offline')}Офлайн",
                payload={"cmd": "set", "key": "format", "value": "offline"},
            )
        )
        keyboard.row()
        keyboard.add(
            Callback(
                f"{mark(user.scope_pref == 'both')}РФ + мир",
                payload={"cmd": "set", "key": "scope", "value": "both"},
            )
        )
        keyboard.add(
            Callback(
                f"{mark(user.scope_pref == 'ru')}РФ",
                payload={"cmd": "set", "key": "scope", "value": "ru"},
            )
        )
        keyboard.add(
            Callback(
                f"{mark(user.scope_pref == 'intl')}Мир",
                payload={"cmd": "set", "key": "scope", "value": "intl"},
            )
        )
        keyboard.row()
        keyboard.add(
            Callback(
                f"{mark(user.digest_enabled)}Дайджест вкл",
                payload={"cmd": "set", "key": "digest", "value": "on"},
            )
        )
        keyboard.add(
            Callback(
                f"{mark(not user.digest_enabled)}Дайджест выкл",
                payload={"cmd": "set", "key": "digest", "value": "off"},
            )
        )
        keyboard.row()
        keyboard.add(Callback("Расписание", payload={"cmd": "sched"}))
        keyboard.add(Text("Меню"))
        return keyboard.get_json()

    def _results_keyboard(self, items, page: int, pages_count: int, mask: int) -> str:
        keyboard = Keyboard(one_time=False, inline=False)
        for index, item in enumerate(items):
            label = f"🌐 {(item.title or 'Открыть')[:28]}"
            keyboard.add(OpenLink(item.url, label))
            if (index + 1) % 2 == 0:
                keyboard.row()
        if items and len(items) % 2:
            keyboard.row()
        nav_buttons = []
        if page > 0:
            nav_buttons.append(
                Callback("⬅️", payload={"cmd": "pg", "mask": mask, "page": page - 1})
            )
        if page + 1 < pages_count:
            nav_buttons.append(
                Callback("➡️", payload={"cmd": "pg", "mask": mask, "page": page + 1})
            )
        nav_buttons.append(
            Callback("📜 Все", payload={"cmd": "all", "mask": mask})
        )
        if nav_buttons:
            for button in nav_buttons:
                keyboard.add(button)
            keyboard.row()
        keyboard.add(Callback("⬅️ Назад", payload={"cmd": "back", "mask": mask}))
        keyboard.add(Text("Меню"))
        return keyboard.get_json()

    async def _send_to_event(
        self,
        event: MessageEvent,
        text: str,
        keyboard: str | None = None,
        *,
        edit: bool = True,
    ) -> None:
        if edit:
            cmid = event.conversation_message_id
            message_id = None if cmid else self._last_message.get(int(event.peer_id))
            if cmid or message_id:
                try:
                    if cmid:
                        await self.bot.api.messages.edit(
                            peer_id=event.peer_id,
                            cmid=cmid,
                            message=text[:4090],
                            keyboard=keyboard,
                        )
                    else:
                        await self.bot.api.messages.edit(
                            peer_id=event.peer_id,
                            message_id=message_id,
                            message=text[:4090],
                            keyboard=keyboard,
                        )
                    return
                except Exception:
                    logger.warning(
                        "vk edit failed; sending a new message", exc_info=True
                    )
        await self._send_ui(event.peer_id, text, keyboard)
        await asyncio.sleep(0.35)

    async def _ack_event(
        self, event: MessageEvent, cmd: str, *, feedback: str | None = None
    ) -> None:
        feedback = feedback or {
            "find": "Ищу…",
            "all": "Собираю…",
            "digest_now": "Проверяю…",
            "hide": "Скрыто",
            "fav": "В избранном",
            "set": "Сохранено",
            "sdset": "Сохранено",
            "sm": "Сохранено",
            "subs_all": "Сохранено",
            "subs_none": "Сохранено",
        }.get(cmd, "")
        try:
            if feedback:
                await event.show_snackbar(feedback)
            else:
                await event.send_empty_answer()
        except Exception:
            logger.debug("vk event ack failed", exc_info=True)
        if event.event_id:
            self._answered_events.add(event.event_id)
            if len(self._answered_events) > 1000:
                self._answered_events.clear()

    async def _snackbar(self, event: MessageEvent, text: str) -> None:
        if not text:
            return
        if event.event_id and event.event_id in self._answered_events:
            return
        try:
            await event.show_snackbar(text[:90])
            if event.event_id:
                self._answered_events.add(event.event_id)
        except Exception:
            logger.debug("vk snackbar failed", exc_info=True)

    def _mask_all(self) -> int:
        return (1 << len(self.topics.order)) - 1

    def _mask_from_topics(self, topics: list[str]) -> int:
        mask = 0
        for index, topic in enumerate(self.topics.order):
            if topic in topics:
                mask |= 1 << index
        return mask

    def _mask_to_topics(self, mask: int) -> list[str]:
        return [t for i, t in enumerate(self.topics.order) if mask & (1 << i)]

    def _toggle_mask(self, mask: int, index: int) -> int:
        return mask ^ (1 << index)


def _payload(event: MessageEvent) -> dict:
    payload = getattr(event, "payload", None)
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        try:
            value = json.loads(payload)
            return value if isinstance(value, dict) else {}
        except ValueError:
            return {}
    return {}
