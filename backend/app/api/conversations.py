from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.errors import domain_http_error
from app.domain.authorization import ExecutionPrincipal, get_server_principal
from app.domain.errors import AgentDomainError
from app.schemas.agent import (
    ConversationCreateRequest,
    ConversationMessagesResponse,
    ConversationResponse,
    ConversationSummaryResponse,
    ConversationUpdateRequest,
)
from app.services.conversation_service import (
    create_conversation,
    delete_conversation,
    get_conversation,
    list_conversation_messages,
    list_conversations,
    update_conversation_title,
)

router = APIRouter()
PrincipalDependency = Annotated[ExecutionPrincipal, Depends(get_server_principal)]


def _http_error(exc: AgentDomainError) -> HTTPException:
    return domain_http_error(exc)


@router.post("", response_model=ConversationResponse)
def create_conversation_endpoint(
    payload: ConversationCreateRequest,
    principal: PrincipalDependency,
) -> ConversationResponse:
    return ConversationResponse(
        **create_conversation(
            title=payload.title,
            routing_mode=payload.routing_mode,
            enabled_capabilities=payload.enabled_capabilities,
            principal=principal,
        )
    )


@router.get("", response_model=list[ConversationSummaryResponse])
def list_conversations_endpoint(
    principal: PrincipalDependency,
) -> list[ConversationSummaryResponse]:
    return [
        ConversationSummaryResponse(**item) for item in list_conversations(principal)
    ]


@router.patch("/{conversation_id}", response_model=ConversationResponse)
def update_conversation_endpoint(
    conversation_id: str,
    payload: ConversationUpdateRequest,
    principal: PrincipalDependency,
) -> ConversationResponse:
    try:
        return ConversationResponse(
            **update_conversation_title(
                conversation_id,
                title=payload.title,
                principal=principal,
            )
        )
    except AgentDomainError as exc:
        raise _http_error(exc) from exc


@router.delete("/{conversation_id}", status_code=204)
def delete_conversation_endpoint(
    conversation_id: str,
    principal: PrincipalDependency,
) -> None:
    try:
        delete_conversation(conversation_id, principal)
    except AgentDomainError as exc:
        raise _http_error(exc) from exc


@router.get("/{conversation_id}", response_model=ConversationResponse)
def get_conversation_endpoint(
    conversation_id: str,
    principal: PrincipalDependency,
) -> ConversationResponse:
    try:
        return ConversationResponse(**get_conversation(conversation_id, principal))
    except AgentDomainError as exc:
        raise _http_error(exc) from exc


@router.get(
    "/{conversation_id}/messages",
    response_model=ConversationMessagesResponse,
)
def list_conversation_messages_endpoint(
    conversation_id: str,
    principal: PrincipalDependency,
    limit: int = Query(default=30, ge=1, le=100),
    before: str | None = Query(default=None, min_length=1, max_length=128),
) -> ConversationMessagesResponse:
    try:
        return ConversationMessagesResponse(
            **list_conversation_messages(
                conversation_id,
                limit=limit,
                before=before,
                principal=principal,
            )
        )
    except AgentDomainError as exc:
        raise _http_error(exc) from exc
