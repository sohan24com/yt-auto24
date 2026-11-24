class GeminiUnavailableError(RuntimeError):
    """Raised after bounded attempts across the configured Gemini keys and models."""


def gemini(prompt, system=None, grounding=False, json_out=True, temperature=0.9,
           tries=None, request_timeout=90, total_timeout=300):
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 32000},
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if grounding:
        body["tools"] = [{"google_search": {}}]
    elif json_out:
        body["generationConfig"]["responseMimeType"] = "application/json"

    keys = list(dict.fromkeys(k.strip() for k in (
        env("GEMINI_API_KEY", False), env("GEMINI_API_KEY_2", False),
        env("GEMINI_API_KEY_3", False), env("GEMINI_API_KEY_4", False),
        env("GEMINI_API_KEY_5", False),
    ) if k and k.strip()))
    if not keys:
        raise GeminiUnavailableError("No Gemini API keys are configured")

    models = _model_list(keys[0])
    if not models:
        raise GeminiUnavailableError("No Gemini models are available to try")

    deadline = time.monotonic() + max(1.0, float(total_timeout))
    attempts = 0
    last = "no usable response"
    status_counts = {}

    for model in models:
        overloaded = False
        for key in keys:
            if tries is not None and attempts >= tries:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            attempts += 1
            gc = dict(body["generationConfig"])
            if not model.startswith("gemini-3"):
                gc["temperature"] = temperature
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            try:
                r = requests.post(
                    url, params={"key": key},
                    json=dict(body, generationConfig=gc),
                    timeout=min(float(request_timeout), remaining),
                )
                if r.status_code == 200:
                    parts = r.json()["candidates"][0]["content"]["parts"]
                    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
                    return extract_json(text) if json_out else text

                status = int(r.status_code)
                status_counts[status] = status_counts.get(status, 0) + 1
                last = f"{model}: HTTP {status} {r.text[:160]}"
                print(f"Gemini candidate unavailable: {model} HTTP {status}")

                if status == 503 or status in (500, 502, 504):
                    # Capacity errors are model-wide; move to another model instead of
                    # asking every account to hit the same overloaded endpoint.
                    overloaded = True
                    break
                if status in (400, 404):
                    # Invalid request/model: another key will not repair this candidate.
                    break
                if status in (401, 403, 429):
                    # These may be key/project-specific, so rotate to the next distinct key.
                    continue
                if status >= 400:
                    break
            except requests.Timeout:
                last = f"{model}: request timed out"
                continue
            except Exception as ex:
                last = f"{model}: {type(ex).__name__}: {str(ex)[:120]}"
                continue

        if tries is not None and attempts >= tries:
            break
        if deadline - time.monotonic() <= 0:
            break
        if overloaded:
            time.sleep(min(2.0, max(0.0, deadline - time.monotonic())))

    summary = ", ".join(f"HTTP {code} x{count}" for code, count in sorted(status_counts.items()))
    if not summary:
        summary = f"{attempts} request attempt(s)"
    raise GeminiUnavailableError(
        f"Gemini unavailable after {attempts} bounded attempt(s) ({summary}); last response: {last}"
    )


# ---------- notifications ----------
def notify(msg):
    tok, chat = env("TELEGRAM_BOT_TOKEN", False), env("TELEGRAM_CHAT_ID", False)
    print("NOTIFY:", msg)
    if tok and chat:
        try:
            requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                          data={"chat_id": chat, "text": msg[:3500]}, timeout=20)
        except Exception:
            pass
