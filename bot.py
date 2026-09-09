import os
import sys
import time
import json
import subprocess
import requests

# نمایش آنی تمام لاگ‌ها در کنسول گیت‌هاب
sys.stdout.reconfigure(line_buffering=True)

RUBIKA_TOKEN = "CDJJFE0VZBQFANITRNGPDRBAJXSJBUVJSJOEJFQOJNAKSBFYHCDRPRQRMUVIUBDO"
BALE_TOKEN = "628083238:xSdEBoOooDiIVRPwAfL8eOmxtOqIhGUk4DI"
PASSWORDS = ["313hekmat", "۳۱۳hekmat"]

RUBIKA_API = f"https://botapi.rubika.ir/v3/{RUBIKA_TOKEN}"
BALE_API = f"https://tapi.bale.ai/bot{BALE_TOKEN}"
DATA_FILE = "bot_data.json"

def normalize(text):
    if not text:
        return ""
    persian_digits = "۰۱۲۳۴۵۶۷۸۹"
    for i, d in enumerate(persian_digits):
        text = text.replace(d, str(i))
    return text.strip().lower()

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"auth_users": [], "bale_chat_id": None}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)

data_store = load_data()

def send_rubika_msg(chat_id, text):
    url = f"{RUBIKA_API}/sendMessage"
    payload = {"chat_id": str(chat_id), "text": text}
    try:
        r = requests.post(url, json=payload, timeout=10)
        print(f"[Rubika Send] to {chat_id}: status={r.status_code}, resp={r.text}", flush=True)
    except Exception as e:
        print(f"[Rubika Send Error]: {e}", flush=True)

def get_rubika_file_url(file_id):
    url = f"{RUBIKA_API}/getFile"
    payload = {"file_id": str(file_id)}
    try:
        res = requests.post(url, json=payload, timeout=15).json()
        data_obj = res.get("data", {}) if isinstance(res.get("data"), dict) else res
        return data_obj.get("download_url") or res.get("download_url")
    except Exception as e:
        print(f"[Rubika File Error]: {e}", flush=True)
        return None

def send_bale_msg(chat_id, text):
    url = f"{BALE_API}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"[Bale Send Error]: {e}", flush=True)

def send_bale_audio(chat_id, file_path, title="Audio"):
    url = f"{BALE_API}/sendAudio"
    try:
        with open(file_path, "rb") as f:
            files = {"audio": (os.path.basename(file_path), f, "audio/mpeg")}
            data = {"chat_id": chat_id, "caption": f"🎵 {title}"}
            res = requests.post(url, data=data, files=files, timeout=180)
            return res.json().get("ok", False)
    except Exception as e:
        print(f"[Bale Audio Error]: {e}", flush=True)
        return False

def get_audio_duration(file_path):
    try:
        cmd = [
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", file_path
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return float(res.stdout.strip())
    except Exception:
        return 0.0

def convert_to_mp3(input_path, output_path):
    cmd = ["ffmpeg", "-y", "-i", input_path, "-vn", "-c:a", "libmp3lame", "-b:a", "128k", output_path]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def compress_audio(input_path, output_path, target_size_mb=48):
    duration = get_audio_duration(input_path)
    if duration <= 0:
        cmd = ["ffmpeg", "-y", "-i", input_path, "-vn", "-c:a", "libmp3lame", "-b:a", "48k", output_path]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return

    target_bytes = target_size_mb * 1024 * 1024
    bitrate_kbps = int((target_bytes * 8) / duration / 1000)
    bitrate_kbps = max(24, min(bitrate_kbps, 128))

    cmd = ["ffmpeg", "-y", "-i", input_path, "-vn", "-c:a", "libmp3lame", "-b:a", f"{bitrate_kbps}k", output_path]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def process_and_forward(file_id, original_name, rubika_chat_id):
    bale_chat_id = data_store.get("bale_chat_id")
    if not bale_chat_id:
        send_rubika_msg(rubika_chat_id, "⚠️ ابتدا یک پیام به ربات بله بفرستید تا شما را شناسایی کند.")
        return

    send_rubika_msg(rubika_chat_id, "⏳ در حال دریافت فایل...")
    download_url = get_rubika_file_url(file_id)
    if not download_url:
        send_rubika_msg(rubika_chat_id, "❌ دریافت آدرس دانلود با خطا مواجه شد.")
        return

    raw_path = f"raw_{file_id}"
    mp3_path = f"out_{file_id}.mp3"
    comp_path = f"comp_{file_id}.mp3"

    try:
        with requests.get(download_url, stream=True, timeout=90) as r:
            with open(raw_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=16384):
                    f.write(chunk)

        send_rubika_msg(rubika_chat_id, "🔄 در حال تبدیل به MP3...")
        convert_to_mp3(raw_path, mp3_path)
        final_file = mp3_path

        size_mb = os.path.getsize(mp3_path) / (1024 * 1024)
        if size_mb > 49.0:
            send_rubika_msg(rubika_chat_id, f"📦 حجم فایل ({size_mb:.1f}MB) بیش از حد بله است؛ در حال فشرده‌سازی...")
            compress_audio(mp3_path, comp_path, target_size_mb=48)
            final_file = comp_path

        send_rubika_msg(rubika_chat_id, "🚀 در حال تحویل به ربات بله...")
        if send_bale_audio(bale_chat_id, final_file, title=original_name or "Voice"):
            send_rubika_msg(rubika_chat_id, "✅ فایل با موفقیت در بله تحویل داده شد!")
        else:
            send_rubika_msg(rubika_chat_id, "❌ خطایی در ارسال به بله رخ داد.")

    except Exception as e:
        print(f"Error processing: {e}", flush=True)
        send_rubika_msg(rubika_chat_id, "❌ خطا در پردازش فایل.")
    finally:
        for p in [raw_path, mp3_path, comp_path]:
            if os.path.exists(p):
                os.remove(p)

def check_bale_updates(offset):
    try:
        res = requests.get(f"{BALE_API}/getUpdates", params={"offset": offset, "timeout": 1}, timeout=4).json()
        if res.get("ok"):
            for upd in res.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message", {})
                cid = msg.get("chat", {}).get("id")
                if cid and data_store.get("bale_chat_id") != cid:
                    data_store["bale_chat_id"] = cid
                    save_data(data_store)
                    print(f"[Bale Connected] Chat ID: {cid}", flush=True)
                    send_bale_msg(cid, "✅ ربات آماده دریافت فایل‌های روبیکا است!")
    except Exception:
        pass
    return offset

def run_bot():
    print(">>> ربات با موفقیت فعال شد و در حال شنود است...", flush=True)
    rubika_offset = None
    bale_offset = 0

    while True:
        bale_offset = check_bale_updates(bale_offset)

        try:
            payload = {"limit": 10}
            if rubika_offset:
                payload["offset_id"] = str(rubika_offset)

            r = requests.post(f"{RUBIKA_API}/getUpdates", json=payload, timeout=10)
            res = r.json()

            # استخراج ساختار داده روبیکا
            data_obj = res.get("data", {}) if isinstance(res.get("data"), dict) else res
            updates = data_obj.get("updates") or []
            if data_obj.get("next_offset_id"):
                rubika_offset = data_obj.get("next_offset_id")

            for item in updates:
                # پشتیبانی از ساختارهای مختلف Update
                upd = item.get("update", item)
                msg = upd.get("new_message", {})
                chat_id = upd.get("chat_id")
                sender_id = msg.get("sender_id")
                raw_text = (msg.get("text") or "").strip()
                norm_text = normalize(raw_text)
                file_info = msg.get("file")

                if norm_text == "/start":
                    send_rubika_msg(chat_id, "سلام! خوش آمدید.\nلطفاً رمز عبور را ارسال کنید:")
                    continue

                if norm_text == "313hekmat":
                    if sender_id not in data_store["auth_users"]:
                        data_store["auth_users"].append(sender_id)
                        save_data(data_store)
                    send_rubika_msg(chat_id, "🔓 رمز تایید شد!\nاز حالا هر فایلی بفرستید، به فایل صوتی MP3 تبدیل شده و در بله تحویلتان داده می‌شود.")
                    continue

                if sender_id not in data_store["auth_users"]:
                    send_rubika_msg(chat_id, "🔒 دسترسی مسدود است. لطفاً ابتدا رمز عبور را ارسال کنید.")
                    continue

                if file_info:
                    file_id = file_info.get("file_id")
                    file_name = file_info.get("file_name", "audio.mp3")
                    process_and_forward(file_id, file_name, chat_id)

        except Exception as e:
            print(f"[Polling Error]: {e}", flush=True)

        time.sleep(2)

if __name__ == "__main__":
    run_bot()
