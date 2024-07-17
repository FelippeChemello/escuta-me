import modal
import os
import json
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse

pretained_whisper_model = "/models/whisper"

def download_whisper_model():
    print("[DOWNLOAD_WHISPER_MODEL] Downloading model...")

    from faster_whisper import download_model

    download_model("small", pretained_whisper_model)

    print("[DOWNLOAD_WHISPER_MODEL] Model downloaded.")  

image = modal.Image.debian_slim().apt_install(
    "git",
    "ffmpeg",
    "libsndfile1",
).pip_install(
    "faster-whisper",
    "transformers",
    "torch",
    "torchaudio",
    "git+https://github.com/m-bain/whisperx.git",
    "requests",
    "python-ffmpeg",
).run_function(download_whisper_model)

app = modal.App(name = "escuta-me", image = image, secrets=[modal.Secret.from_name("escuta-me")])
web_app = FastAPI()

@app.function(secrets=[modal.Secret.from_name("escuta-me")])
def send_message(recipient: str, message: str):
    import requests
    
    ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN")
    VERSION = os.getenv("META_VERSION")
    PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")

    url = f"https://graph.facebook.com/{VERSION}/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": "Bearer " + ACCESS_TOKEN,
        "Content-Type": "application/json",
    }
    data = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient,
        "type": "text",
        "text": {"preview_url": False, "body": message},
    }
    response = requests.post(url, headers=headers, json=data)
    return response

@app.function(secrets=[modal.Secret.from_name("escuta-me")], gpu="any", timeout=60)
def speech_to_text(media_id):
    import requests

    ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN")
    VERSION = os.getenv("META_VERSION")

    url = f"https://graph.facebook.com/{VERSION}/{media_id}"
    headers = {
        "Authorization": "Bearer " + ACCESS_TOKEN,
    }
    response = requests.get(url, headers=headers)
    media_url = response.json()['url']

    print(f"Downloading media from {media_url}")

    media = requests.get(media_url, headers=headers)
    audio_path = "media.ogg"
    with open(audio_path, "wb") as file:
        file.write(media.content)

    print("Converting ogg to wav")
    import ffmpeg
    ffmpeg.input(audio_path).output("media.wav").run()

    print(f"Starting Transcription processing")
    import whisperx

    whisper_model = whisperx.load_model(pretained_whisper_model, device="cuda", compute_type="float16")
    audio = whisperx.load_audio(media)

    print("Transcribing audio")

    result = whisper_model.transcribe(audio)

    print("Transcription completed")
    print(result['segments'])

    return result['segments']

@web_app.post("/webhook")
async def webhook(request: Request):
    body = await request.json()

    wa_id = body['entry'][0]['id']
    message_type = body['entry'][0]['changes'][0]['value']['messages'][0]['type']
    contact_name = body['entry'][0]['changes'][0]['value']['contacts'][0]['profile']['name']

    print(f"New {message_type} message from {contact_name} ({wa_id})")
    print(json.dumps(body, indent=2))

    if message_type == "text":
        message = body['entry'][0]['changes'][0]['value']['messages'][0]['text']['body']
        print(f"Message: {message}")
    elif message_type == "audio":
        audio_id = body['entry'][0]['changes'][0]['value']['messages'][0]['audio']['id']
        print(f"Audio ID: {audio_id}")

        transcription = speech_to_text.remote(audio_id)

    return {"status": "ok"}

@web_app.get("/webhook")
async def verify(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return PlainTextResponse(challenge)
    else:
        return {"status": "error"}
    

@app.function()
@modal.asgi_app()
def fastapi_app():
    return web_app