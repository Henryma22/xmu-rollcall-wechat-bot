from __future__ import annotations

import argparse
import asyncio
import os
import shlex
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Union

from .config import CONFIG_DIR, ensure_config_dir
from .rollcall_service import AnswerBatchResult, AnswerOutcome, RollcallService
from .utils import save_session
from .wechat_storage import (
    add_or_update_user_account,
    build_user_session_cache_key,
    get_current_user_account,
    get_user_accounts,
    set_current_user_account,
)

MAX_LINES_PER_MESSAGE = 12
MAX_CHARS_PER_MESSAGE = 380
SUMMARY_ROWS_PER_MESSAGE = 4
DETAIL_BLOCKS_PER_MESSAGE = 2

ReplyPayload = Union[str, List[str], None]


@dataclass
class PendingConfigState:
    stage: str
    username: str = ""


def _escape_markdown(text: Any) -> str:
    value = str(text or "").replace("\r", " ").replace("\n", " ").strip()
    if not value:
        return "-"
    return value.replace("`", "'").replace("|", "｜")


def _shorten(text: Any, max_len: int = 12) -> str:
    value = _escape_markdown(text)
    if len(value) <= max_len:
        return value
    return f"{value[: max_len - 1]}…"


def _account_id_text(account: Optional[dict]) -> str:
    if not account:
        return "-"
    return f"`{account.get('id')}`"


def _compact_time(value: str) -> str:
    if len(value) >= 16:
        return value[5:16]
    return value


def _build_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    header_line = "| " + " | ".join(headers) + " |"
    separator_line = "| " + " | ".join(["---"] * len(headers)) + " |"
    row_lines = ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join([header_line, separator_line, *row_lines])


def _chunked(items: Sequence[Any], size: int) -> List[Sequence[Any]]:
    return [items[index:index + size] for index in range(0, len(items), size)]


def _split_long_markdown_message(text: str) -> List[str]:
    stripped = text.strip()
    if not stripped:
        return []
    if (
        stripped.count("\n") + 1 <= MAX_LINES_PER_MESSAGE
        and len(stripped) <= MAX_CHARS_PER_MESSAGE
    ):
        return [stripped]

    blocks = stripped.split("\n\n")
    messages: List[str] = []
    current_blocks: List[str] = []
    current_lines = 0
    current_chars = 0

    for block in blocks:
        block_lines = block.count("\n") + 1
        block_chars = len(block)
        needs_flush = current_blocks and (
            current_lines + 1 + block_lines > MAX_LINES_PER_MESSAGE
            or current_chars + 2 + block_chars > MAX_CHARS_PER_MESSAGE
        )
        if needs_flush:
            messages.append("\n\n".join(current_blocks).strip())
            current_blocks = [block]
            current_lines = block_lines
            current_chars = block_chars
            continue
        current_blocks.append(block)
        if len(current_blocks) == 1:
            current_lines = block_lines
            current_chars = block_chars
        else:
            current_lines += 1 + block_lines
            current_chars += 2 + block_chars

    if current_blocks:
        messages.append("\n\n".join(current_blocks).strip())

    return messages


def _normalize_messages(payload: ReplyPayload) -> List[str]:
    if payload is None:
        return []
    if isinstance(payload, str):
        payload = [payload]

    messages: List[str] = []
    for item in payload:
        messages.extend(_split_long_markdown_message(item))
    return [message for message in messages if message.strip()]


def _format_accounts_table(accounts: List[dict], current_account_id: Optional[int]) -> str:
    if not accounts:
        return _build_table(
            ["ID", "姓名", "状态"],
            [["-", "-", "未配置"]],
        )

    rows = []
    for account in accounts:
        account_id = int(account.get("id", 0))
        display_name = _shorten(account.get("name") or account.get("username") or "-", 8)
        status = "当前" if account_id == int(current_account_id or 0) else "-"
        rows.append([f"`{account_id}`", display_name, status])
    return _build_table(["ID", "姓名", "状态"], rows)


def _format_help_markdown() -> str:
    return "\n".join(
        [
            "# 命令",
            "",
            _build_table(
                ["指令", "说明"],
                [
                    ["`/conf`", "分步配置账号"],
                    ["`/switch 1`", "切换账号"],
                    ["`/accounts`", "查看账号 ID"],
                    ["`/answer`", "查询并应答"],
                    ["`/refresh`", "清理登录缓存"],
                    ["`/cancel`", "取消 `/conf`"],
                ],
            ),
            "",
            "> `/conf` 后依次发送学号、密码。",
        ]
    )


def _format_no_account_markdown() -> str:
    return "\n".join(
        [
            "# 尚未配置",
            "",
            _build_table(
                ["步骤", "操作"],
                [
                    ["1", "`/conf`"],
                    ["2", "发送学号"],
                    ["3", "发送密码"],
                    ["4", "`/answer`"],
                ],
            ),
        ]
    )


def _format_accounts_markdown(user_id: str) -> str:
    accounts = get_user_accounts(user_id)
    current_account = get_current_user_account(user_id)
    return "\n".join(
        [
            "# 账号",
            "",
            f"当前 ID：{_account_id_text(current_account)}",
            "",
            _format_accounts_table(accounts, (current_account or {}).get("id")),
        ]
    )


def _format_conf_start_markdown() -> str:
    return "\n".join(
        [
            "# 配置账号",
            "",
            "请发送学号。",
            "",
            "> 密码会作为普通消息发送，完成后可自行撤回。",
        ]
    )


def _format_conf_password_markdown(username: str) -> str:
    return "\n".join(
        [
            "# 配置账号",
            "",
            f"学号已记录：`{_shorten(username, 18)}`",
            "",
            "请发送密码。",
            "",
            "> 可发送 `/cancel` 取消。",
        ]
    )


def _format_conf_retry_markdown(message: str) -> str:
    return "\n".join(
        [
            "# 登录失败",
            "",
            f"- {_shorten(message, 40)}",
            "- 请重新发送密码。",
            "- 或发送 `/cancel` 取消。",
        ]
    )


def _format_conf_cancelled_markdown() -> str:
    return "\n".join(
        [
            "# 已取消",
            "",
            "账号配置已中止。",
        ]
    )


def _format_conf_success_messages(account: dict, created: bool, accounts: List[dict]) -> List[str]:
    action_title = "# 账号已添加" if created else "# 账号已更新"
    return [
        "\n".join(
            [
                action_title,
                "",
                f"当前 ID：{_account_id_text(account)}",
                "",
                _format_accounts_table(accounts, account.get("id")),
            ]
        ),
        "\n".join(
            [
                "# 下一步",
                "",
                _build_table(
                    ["指令", "作用"],
                    [
                        ["`/answer`", "查一次签到"],
                        ["`/switch ID`", "切换账号"],
                    ],
                ),
            ]
        ),
    ]


def _format_switch_markdown(account: dict, accounts: List[dict]) -> str:
    return "\n".join(
        [
            "# 已切换",
            "",
            f"当前 ID：{_account_id_text(account)}",
            "",
            _format_accounts_table(accounts, account.get("id")),
        ]
    )


def _format_refresh_markdown(account: dict, removed: bool) -> str:
    status = "已清理，下次会重新登录。" if removed else "当前没有可清理的缓存。"
    return "\n".join(
        [
            "# 缓存处理完成",
            "",
            _build_table(
                ["当前 ID", "结果"],
                [[_account_id_text(account), status]],
            ),
        ]
    )


def _format_answer_status(outcome: AnswerOutcome) -> str:
    if outcome.action == "already_answered":
        return "已完成"
    if outcome.action == "unsupported":
        return "暂不支持"
    if outcome.action == "expired":
        return "已过期"
    return "成功" if outcome.success else "失败"


def _format_no_rollcall_markdown(account: dict, queried_at: str) -> str:
    return "\n".join(
        [
            "# 没有签到",
            "",
            _build_table(
                ["当前 ID", "时间"],
                [[_account_id_text(account), f"`{_compact_time(queried_at)}`"]],
            ),
        ]
    )


def _detail_text(outcome: AnswerOutcome) -> str:
    if outcome.number_code:
        return f"码 `{_escape_markdown(outcome.number_code)}`"
    if outcome.latitude is not None and outcome.longitude is not None:
        return f"`{outcome.latitude:.5f}, {outcome.longitude:.5f}`"
    if outcome.response_status and not outcome.success:
        return f"HTTP `{outcome.response_status}`"
    if outcome.action in {"unsupported", "failed", "expired", "skipped"}:
        return _shorten(outcome.message, 18)
    return "-"


def _format_answer_messages(batch_result: AnswerBatchResult) -> List[str]:
    account = batch_result.account
    queried_at = batch_result.queried_at.strftime("%Y-%m-%d %H:%M:%S")
    messages: List[str] = [
        "\n".join(
            [
                "# 签到结果",
                "",
                _build_table(
                    ["当前 ID", "时间", "数量"],
                    [[
                        _account_id_text(account),
                        f"`{_compact_time(queried_at)}`",
                        f"`{len(batch_result.rollcalls)}`",
                    ]],
                ),
            ]
        )
    ]

    summary_rows = []
    detail_blocks = []

    for index, outcome in enumerate(batch_result.outcomes, start=1):
        rollcall = outcome.rollcall
        summary_rows.append(
            [
                f"`{index}`",
                _shorten(rollcall.course_title, 10),
                _shorten(rollcall.type_label, 4),
                _format_answer_status(outcome),
            ]
        )

        detail_value = _detail_text(outcome)
        if detail_value == "-":
            continue
        detail_blocks.append(
            "\n".join(
                [
                    f"## 详情 `{index}`",
                    "",
                    _build_table(
                        ["项目", "内容"],
                        [
                            ["课程", _shorten(rollcall.course_title, 16)],
                            ["类型", _shorten(rollcall.type_label, 8)],
                            ["结果", _format_answer_status(outcome)],
                            ["详情", detail_value],
                        ],
                    ),
                ]
            )
        )

    for summary_chunk in _chunked(summary_rows, SUMMARY_ROWS_PER_MESSAGE):
        messages.append(
            "\n".join(
                [
                    "# 列表",
                    "",
                    _build_table(
                        ["#", "课程", "类型", "结果"],
                        list(summary_chunk),
                    ),
                ]
            )
        )

    for block_chunk in _chunked(detail_blocks, DETAIL_BLOCKS_PER_MESSAGE):
        messages.append("\n\n".join(block_chunk))

    return messages


def _format_error_markdown(title: str, message: str) -> str:
    return "\n".join(
        [
            f"# {title}",
            "",
            f"- {_shorten(message, 40)}",
        ]
    )


class XMUWeChatBotApp:
    def __init__(self, bot: Any):
        self.bot = bot
        self.command_lock = asyncio.Lock()
        self.pending_configs: Dict[str, PendingConfigState] = {}

    async def handle_message(self, msg: Any) -> None:
        text = (getattr(msg, "text", "") or "").strip()
        if not text:
            return

        async with self.command_lock:
            try:
                await self.bot.send_typing(msg.user_id)
            except Exception:
                pass

            try:
                reply_payload = await self._route_message(msg, text)
            except Exception as exc:
                reply_payload = _format_error_markdown("执行失败", str(exc))

            await self._send_messages(msg, reply_payload)

    async def _send_messages(self, msg: Any, payload: ReplyPayload) -> None:
        messages = _normalize_messages(payload)
        for index, message in enumerate(messages):
            await self.bot.reply(msg, message)
            if index < len(messages) - 1:
                await asyncio.sleep(0.2)

    async def _route_message(self, msg: Any, text: str) -> ReplyPayload:
        if msg.user_id in self.pending_configs:
            return await self._handle_pending_conf(msg.user_id, text)

        if not text.startswith("/"):
            return None

        return await self._dispatch(msg, text)

    async def _dispatch(self, msg: Any, text: str) -> ReplyPayload:
        try:
            parts = shlex.split(text)
        except ValueError as exc:
            return _format_error_markdown("命令解析失败", str(exc))

        if not parts:
            return None

        command = parts[0].lower()
        if command == "/help":
            return _format_help_markdown()
        if command == "/accounts":
            return await asyncio.to_thread(_format_accounts_markdown, msg.user_id)
        if command == "/conf":
            return self._start_conf(msg.user_id)
        if command == "/switch":
            return await self._handle_switch(msg.user_id, parts)
        if command == "/answer":
            return await self._handle_answer(msg.user_id)
        if command == "/refresh":
            return await self._handle_refresh(msg.user_id)
        if command == "/cancel":
            return _format_error_markdown("没有进行中的配置", "当前无需取消。")

        return [
            _format_error_markdown("未知命令", parts[0]),
            _format_help_markdown(),
        ]

    def _start_conf(self, user_id: str) -> str:
        self.pending_configs[user_id] = PendingConfigState(stage="username")
        return _format_conf_start_markdown()

    async def _handle_pending_conf(self, user_id: str, text: str) -> ReplyPayload:
        if text.lower() == "/cancel":
            self.pending_configs.pop(user_id, None)
            return _format_conf_cancelled_markdown()

        if text.startswith("/"):
            if text.lower() == "/conf":
                return self._start_conf(user_id)
            return _format_error_markdown("配置进行中", "请继续输入，或发送 `/cancel`。")

        state = self.pending_configs[user_id]
        if state.stage == "username":
            username = text.strip()
            if not username:
                return _format_error_markdown("学号为空", "请重新发送学号。")
            state.username = username
            state.stage = "password"
            return _format_conf_password_markdown(username)

        password = text.strip()
        if not password:
            return _format_error_markdown("密码为空", "请重新发送密码。")

        try:
            validation = await asyncio.to_thread(
                RollcallService.validate_credentials,
                state.username,
                password,
            )
        except Exception as exc:
            return _format_conf_retry_markdown(str(exc))

        account_name = validation.name
        account, created = await asyncio.to_thread(
            add_or_update_user_account,
            user_id,
            state.username,
            password,
            account_name,
        )

        cache_key = build_user_session_cache_key(user_id, int(account["id"]))
        service = RollcallService(account, session_cache_key=cache_key)
        await asyncio.to_thread(save_session, validation.session, service.session_cache_path)
        accounts = await asyncio.to_thread(get_user_accounts, user_id)
        self.pending_configs.pop(user_id, None)
        return _format_conf_success_messages(account, created, accounts)

    async def _handle_switch(self, user_id: str, parts: Sequence[str]) -> ReplyPayload:
        if len(parts) != 2:
            return _format_error_markdown("参数错误", "用法：/switch 账号ID")

        try:
            account_id = int(parts[1])
        except ValueError:
            return _format_error_markdown("参数错误", "账号ID 必须是数字。")

        account = await asyncio.to_thread(set_current_user_account, user_id, account_id)
        if not account:
            accounts = await asyncio.to_thread(get_user_accounts, user_id)
            return [
                _format_error_markdown("切换失败", f"没有找到账号 ID {account_id}。"),
                "\n".join(
                    [
                        "# 账号",
                        "",
                        _format_accounts_table(accounts, None),
                    ]
                ),
            ]

        accounts = await asyncio.to_thread(get_user_accounts, user_id)
        return _format_switch_markdown(account, accounts)

    async def _handle_answer(self, user_id: str) -> ReplyPayload:
        account = await asyncio.to_thread(get_current_user_account, user_id)
        if not account:
            return _format_no_account_markdown()

        cache_key = build_user_session_cache_key(user_id, int(account["id"]))
        service = RollcallService(account, session_cache_key=cache_key)
        batch_result = await asyncio.to_thread(service.answer_active_rollcalls)
        queried_at = batch_result.queried_at.strftime("%Y-%m-%d %H:%M:%S")
        if not batch_result.rollcalls:
            return _format_no_rollcall_markdown(account, queried_at)
        return _format_answer_messages(batch_result)

    async def _handle_refresh(self, user_id: str) -> ReplyPayload:
        account = await asyncio.to_thread(get_current_user_account, user_id)
        if not account:
            return _format_no_account_markdown()

        cache_key = build_user_session_cache_key(user_id, int(account["id"]))
        service = RollcallService(account, session_cache_key=cache_key)
        removed = await asyncio.to_thread(service.clear_session_cache)
        return _format_refresh_markdown(account, removed)


def _resolve_cred_path(cli_cred_path: Optional[str]) -> str:
    if cli_cred_path:
        return cli_cred_path
    if env_cred_path := os.environ.get("XMU_WECHAT_BOT_CRED_PATH"):
        return env_cred_path
    return str(CONFIG_DIR / "wechatbot-credentials.json")


def _load_wechatbot_class():
    try:
        from wechatbot import WeChatBot
    except ImportError as exc:
        raise SystemExit(
            '未安装依赖。请先在项目目录执行: pip install -e .'
        ) from exc
    return WeChatBot


async def _run_bot(args: argparse.Namespace) -> None:
    WeChatBot = _load_wechatbot_class()
    cred_path = _resolve_cred_path(args.cred_path)

    bot = WeChatBot(
        cred_path=cred_path,
        on_qr_url=lambda url: print(f"[wechatbot] 请扫码登录：{url}", flush=True),
        on_scanned=lambda: print("[wechatbot] 已扫码，等待确认。", flush=True),
        on_expired=lambda: print("[wechatbot] 二维码已过期，请重新生成。", flush=True),
        on_error=lambda err: print(f"[wechatbot] 错误：{err}", file=sys.stderr, flush=True),
    )

    app = XMUWeChatBotApp(bot)

    @bot.on_message
    async def handle_message(msg: Any) -> None:
        await app.handle_message(msg)

    await bot.login(force=args.force_login)
    print(f"[wechatbot] 登录成功，凭证位置：{cred_path}", flush=True)

    if args.login_only:
        print("[wechatbot] 已完成登录，按需启动 systemd 服务即可。", flush=True)
        return

    print("[wechatbot] 机器人已启动，等待微信命令消息。", flush=True)
    await bot.start()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="XMU Rollcall WeChat Bot")
    parser.add_argument(
        "--cred-path",
        help="wechatbot 凭证文件路径，默认使用 XMU_WECHAT_BOT_CRED_PATH 或配置目录。",
    )
    parser.add_argument(
        "--force-login",
        action="store_true",
        help="忽略现有 wechatbot 凭证，强制重新扫码登录。",
    )
    parser.add_argument(
        "--login-only",
        action="store_true",
        help="只完成扫码登录并写入凭证，不启动消息监听。",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    ensure_config_dir()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        asyncio.run(_run_bot(args))
    except KeyboardInterrupt:
        print("\n[wechatbot] 已停止。", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
