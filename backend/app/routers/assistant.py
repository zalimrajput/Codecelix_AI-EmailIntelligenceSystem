from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.config import get_settings
from app.schemas import AssistantRequest
from app.security import AuthenticatedUser, require_permission, user_client
from app.services.ai import chat, embed_texts

router = APIRouter(prefix="/assistant", tags=["assistant"])
AssistantUser = Annotated[AuthenticatedUser, Depends(require_permission("ai_assistant:use"))]
SearchUser = Annotated[AuthenticatedUser, Depends(require_permission("rag_search:use"))]
MIN_EMAIL_SIMILARITY = 0.7
MAX_ASSISTANT_REFERENCES = 1
ASSISTANT_RETRIEVAL_CANDIDATES = 50


def _relevant_email_matches(
    matches: Any, *, limit: int = MAX_ASSISTANT_REFERENCES
) -> list[dict[str, Any]]:
    if not isinstance(matches, list):
        return []

    best_by_email: dict[str, dict[str, Any]] = {}
    for item in matches:
        if not isinstance(item, dict):
            continue
        try:
            similarity = float(item.get("similarity"))
        except (TypeError, ValueError):
            continue
        if not MIN_EMAIL_SIMILARITY <= similarity <= 1:
            continue
        email_id = str(item.get("email_id") or "")
        if not email_id:
            continue
        current = best_by_email.get(email_id)
        if current is None or similarity > float(current["similarity"]):
            best_by_email[email_id] = {**item, "similarity": similarity}

    ranked = sorted(
        best_by_email.values(),
        key=lambda item: float(item["similarity"]),
        reverse=True,
    )
    return ranked[:limit]


@router.get("/conversations")
async def conversations(user: AssistantUser) -> list[dict[str, Any]]:
    return await user_client(user).select(
        "ai_conversations",
        {"select": "*", "user_id": f"eq.{user.id}", "order": "updated_at.desc"},
        limit=50,
    )


@router.get("/conversations/{conversation_id}/messages")
async def conversation_messages(conversation_id: str, user: AssistantUser) -> list[dict[str, Any]]:
    db = user_client(user)
    owned_conversations = await db.select(
        "ai_conversations",
        {"select": "id", "id": f"eq.{conversation_id}", "user_id": f"eq.{user.id}"},
        limit=1,
    )
    if not owned_conversations:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return await db.select(
        "ai_conversation_messages",
        {"select": "*", "conversation_id": f"eq.{conversation_id}", "order": "created_at.asc"},
        limit=200,
    )


@router.post("/ask")
async def ask_assistant(body: AssistantRequest, user: AssistantUser) -> dict[str, Any]:
    db = user_client(user)
    conversation_id = body.conversation_id
    if conversation_id:
        conversations = await db.select(
            "ai_conversations",
            {"select": "id", "id": f"eq.{conversation_id}", "user_id": f"eq.{user.id}"},
            limit=1,
        )
        if not conversations:
            raise HTTPException(status_code=404, detail="Conversation not found.")
    else:
        conversation = await db.insert(
            "ai_conversations", {"user_id": user.id, "title": body.message[:120]}
        )
        conversation_id = str(conversation["id"])

    vectors = await embed_texts(
        [body.message], db=db, user_id=user.id, operation="assistant_search",
        task_type="RETRIEVAL_QUERY",
    )
    matches = await db.request(
        "POST",
        "rest/v1/rpc/search_email_chunks",
        json={"query_embedding": "[" + ",".join(str(float(item)) for item in vectors[0]) + "]",
              "match_count": ASSISTANT_RETRIEVAL_CANDIDATES},
    )
    references = _relevant_email_matches(matches)
    for reference in references:
        await db.insert(
            "rag_retrievals",
            {"user_id": user.id, "conversation_id": conversation_id,
             "chunk_id": reference.get("chunk_id"), "query_text": body.message,
             "similarity": reference.get("similarity"),
             "model": get_settings().embedding_model},
        )
    previous = await db.select(
        "ai_conversation_messages",
        {"select": "role,content", "conversation_id": f"eq.{conversation_id}",
         "order": "created_at.desc"},
        limit=10,
    )
    context = "\n\n".join(
        f"[Email {item.get('email_id')} | {item.get('subject') or 'No subject'} | "
        f"{item.get('sender_email') or 'Unknown sender'}]\n{item.get('content') or ''}"
        for item in references
    )
    messages: list[dict[str, str]] = [
        {"role": "system", "content": (
            "You are a private email assistant. Answer only from the user's retrieved email "
            "context and clearly state when the context does not support an answer. Do not "
            "claim to have sent a reply or changed an email.\n\n"
            f"Retrieved emails:\n{context or 'No matching emails were found.'}"
        )}
    ]
    messages.extend(
        {"role": item["role"], "content": item["content"]}
        for item in reversed(previous)
        if item.get("role") in {"user", "assistant"} and item.get("content")
    )
    messages.append({"role": "user", "content": body.message})
    answer = await chat(
        messages, db=db, user_id=user.id, operation="ai_assistant"
    )
    await db.insert(
        "ai_conversation_messages",
        {"conversation_id": conversation_id, "role": "user", "content": body.message},
    )
    assistant_message = await db.insert(
        "ai_conversation_messages",
        {"conversation_id": conversation_id, "role": "assistant", "content": answer,
         "referenced_email_ids": list(dict.fromkeys(
             item["email_id"] for item in references if item.get("email_id")
         )), "ai_model": get_settings().chat_model},
    )
    await db.update(
        "ai_conversations", {"id": f"eq.{conversation_id}", "user_id": f"eq.{user.id}"},
        {"updated_at": datetime.now(UTC).isoformat()},
    )
    return {"conversation_id": conversation_id, "message": assistant_message, "references": references}


@router.get("/search")
async def semantic_search(
    user: SearchUser,
    q: str = Query(min_length=2, max_length=2000),
    limit: int = Query(default=10, ge=1, le=50),
) -> list[dict[str, Any]]:
    vector = (await embed_texts(
        [q], db=user_client(user), user_id=user.id, operation="semantic_search",
        task_type="RETRIEVAL_QUERY",
    ))[0]
    results = await user_client(user).request(
        "POST",
        "rest/v1/rpc/search_email_chunks",
        json={"query_embedding": "[" + ",".join(str(float(item)) for item in vector) + "]",
              "match_count": min(limit * 4, 50)},
    )
    return _relevant_email_matches(results, limit=limit)
