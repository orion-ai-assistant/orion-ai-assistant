from __future__ import annotations

from typing import Any

from api.app.core.message_protocol import MessageKind
from api.app.core.message_schemas import OutgoingSocketMessage
from api.app.core.stream_constants import MessageStatus, PayloadEventName, SenderType, StreamEventType


class MessageFactory:
    @staticmethod
    def system_message(content: str, payload_event: PayloadEventName | str | None = None) -> OutgoingSocketMessage:
        return OutgoingSocketMessage(
            sender=SenderType.SYSTEM,
            content=content,
            kind=MessageKind.SYSTEM,
            payload_event=payload_event,
        )

    @staticmethod
    def user_echo(content: str, message_id: str, client_message_id: str) -> OutgoingSocketMessage:
        return OutgoingSocketMessage(
            sender=SenderType.USER_ECHO,
            content=content,
            kind=MessageKind.USER_ECHO,
            payload_event=PayloadEventName.MESSAGE_ECHO,
            message_id=message_id,
            client_message_id=client_message_id,
        )

    @staticmethod
    def agent_thinking(content: str) -> OutgoingSocketMessage:
        return OutgoingSocketMessage(
            sender=SenderType.BOT_THINKING,
            content=content,
            kind=MessageKind.THINKING,
            is_chunk=True,
            is_thinking=True,
        )

    @staticmethod
    def agent_status(content: str) -> OutgoingSocketMessage:
        return OutgoingSocketMessage(
            sender=SenderType.SYSTEM,
            content=content,
            kind=MessageKind.STATUS,
            payload_event=StreamEventType.STATUS,
            is_chunk=True,
        )

    @staticmethod
    def agent_text(content: str, is_chunk: bool) -> OutgoingSocketMessage:
        return OutgoingSocketMessage(
            sender=SenderType.BOT_STREAM,
            content=content,
            kind=MessageKind.STREAM,
            is_chunk=is_chunk,
        )

    @staticmethod
    def agent_error(content: str) -> OutgoingSocketMessage:
        return OutgoingSocketMessage(
            sender=SenderType.ERROR,
            content=content,
            kind=MessageKind.ERROR,
            status=MessageStatus.ERROR,
            is_done=True,
        )

    @staticmethod
    def tool_call(tool_name: str, tool_args: dict[str, Any]) -> OutgoingSocketMessage:
        return OutgoingSocketMessage(
            sender=SenderType.BOT_TOOL,
            content=f"Tool çağrıldı: {tool_name}",
            kind=MessageKind.TOOL_CALL,
            payload_event=StreamEventType.TOOL_CALL,
            tool_name=tool_name,
            tool_args=tool_args,
            is_tool=True,
        )

    @staticmethod
    def tool_result(tool_name: str, tool_output: str) -> OutgoingSocketMessage:
        short_output = tool_output.strip()
        if len(short_output) > 180:
            short_output = short_output[:180].rstrip() + "..."

        content = f"Tool sonucu ({tool_name}): {short_output}" if short_output else f"Tool tamamlandı: {tool_name}"

        return OutgoingSocketMessage(
            sender=SenderType.BOT_TOOL,
            content=content,
            kind=MessageKind.TOOL_RESULT,
            payload_event=StreamEventType.TOOL_RESULT,
            tool_name=tool_name,
            tool_output=short_output,
            is_tool=True,
        )

    @staticmethod
    def final_done() -> OutgoingSocketMessage:
        return OutgoingSocketMessage(
            sender=SenderType.BOT,
            content="",
            kind=MessageKind.FINAL,
            is_done=True,
        )