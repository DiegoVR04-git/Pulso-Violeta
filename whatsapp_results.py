"""Resultados de la solicitud inicial; no son confirmaciones de entrega o lectura."""


def classify_response(status_code, body):
    unknown = {"status": "unknown", "message_id": None, "error_code": None}
    if not 200 <= status_code < 300:
        error = body.get("error", {}) if isinstance(body, dict) else {}
        code = error.get("code") if isinstance(error, dict) else None
        # Un error del servidor puede ocurrir después de aceptar la solicitud.
        status = "unknown" if status_code >= 500 else "failed"
        return {"status": status, "message_id": None, "error_code": code}

    if not isinstance(body, dict):
        return unknown
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages or not isinstance(messages[0], dict):
        return unknown
    message = messages[0]
    message_id = message.get("id")
    if not isinstance(message_id, str) or not message_id:
        return unknown
    provider_status = message.get("message_status")
    if provider_status in ("held_for_quality_assessment", "paused"):
        status = "pending"
    elif provider_status in (None, "accepted"):
        status = "accepted"
    else:
        status = "unknown"
    return {"status": status, "message_id": message_id, "error_code": None}


def summarize_results(results):
    return {
        "total": len(results),
        **{status: sum(item["status"] == status for item in results)
           for status in ("accepted", "pending", "failed", "unknown")},
    }
