import os
import sys
import time
import json
import subprocess
import requests

# ==================== تنظیمات و توکن‌ها ====================
RUBIKA_TOKEN = "CDJJFE0VZBQFANITRNGPDRBAJXSJBUVJSJOEJFQOJNAKSBFYHCDRPRQRMUVIUBDO"
BALE_TOKEN = "628083238:xSdEBoOooDiIVRPwAfL8eOmxtOqIhGUk4DI"
PASSWORD = "313hekmat"

RUBIKA_API = f"https://botapi.rubika.ir/v3/{RUBIKA_TOKEN}"
BALE_API = f"https://tapi.bale.ai/bot{BALE_TOKEN}"

# فایل ذخیره وضعیت احراز هویت و شناسه بله
DATA_FILE = "bot_data.json"

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

# ==================== توابع روبیکا ====================
def send_rubika_msg(chat_id, text):
    url = f"{RUBIKA_API}/sendMessage"
    payload = {"chat_id": str(chat_id), "text": text}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Error sending Rubika message: {e}")

def get_rubika_file_url(file_id):
    url = f"{RUBIKA_API}/getFile"
    payload = {"file_id": str(file_id)}
    try:
        res = requests.post(url, json=payload, timeout=15).json()
        return res.get("download_url")
    except Exception as e:
        print(f"Error getting Rubika file: {e}")
        return None

# ==================== توابع بله ====================
def send_bale_msg(chat_id, text):
    url = f"{BALE_API}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Error sending Bale message: {e}")

def send_bale_audio(chat_id, file_path, title="Audio"):
    url = f"{BALE_API}/sendAudio"
    try:
        with open(file_path, "rb") as f:
            files = {"audio": (os.path.basename(file_path), f, "audio/mpeg")}
            data = {"chat_id": chat_id, "caption": f"🎵 {title}"}
            res = requests.post(url, data=data, files=files, timeout=120)
            return res.json().get("ok", False)
    except Exception as e:
        print(f"Error sending Bale audio: {e}")
        return False

# ==================== تبدیل و فشرده‌سازی با FFmpeg ====================
def get_audio_duration(file_path):
    try:
        cmd = [
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", file_path
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return float(result.stdout.strip())
    except Exception:
        return 0.0

def convert_to_mp3(input_path, output_path):
    cmd = ["ffmpeg", "-y", "-i", input_path, "-vn", "-c:a", "libmp3lame", "-b:a", "128k", output_path]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def compress_audio(input_path, output_path, target_size_mb=48):
    duration = get_audio_duration(input_path)
    if duration <= 0:
        cmd = ["ffmpeg", "-y", "-i", input_path, "-vn", "-c:a", "libmp3lame", "-b:a", "64k", output_path]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return

    # محاسبه بیت‌ریت بهینه برای نگه داشتن حجم زیر ۵۰ مگابایت
    target_bytes = target_size_mb * 1024 * 1024
    bitrate_kbps = int((target_bytes * 8) / duration / 1000)
    bitrate_kbps = max(24, min(bitrate_kbps, 128))

    cmd = ["ffmpeg", "-y", "-i", input_path, "-vn", "-c:a", "libmp3lame", "-b:a", f"{bitrate_kbps}k", output_path]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# ==================== پردازش فایل‌ها ====================
def process_and_forward(file_id, original_name, rubika_chat_id):
    bale_chat_id = data_store.get("bale_chat_id")
    if not bale_chat_id:
        send_rubika_msg(rubika_chat_id, "⚠️ شناسه ربات بله شما هنوز ثبت نشده است!\nلطفاً یک بار وارد ربات بله شده و پیام 313hekmat را ارسال کنید.")
        return

    send_rubika_msg(rubika_chat_id, "⏳ در حال دریافت و پردازش فایل...")
    download_url = get_rubika_file_url(file_id)
    if not download_url:
        send_rubika_msg(rubika_chat_id, "❌ دریافت لینک دانلود از روبیکا ناموفق بود.")
        return

    raw_path = f"temp_input_{file_id}"
    mp3_path = f"output_{file_id}.mp3"
    compressed_path = f"compressed_{file_id}.mp3"

    try:
        # دانلود فایل ورودی
        with requests.get(download_url, stream=True, timeout=60) as r:
            with open(raw_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)

        # تبدیل به MP3
        convert_to_mp3(raw_path, mp3_path)
        final_file = mp3_path

        # بررسی حجم (محدودیت ۵۰ مگابایت بله)
        size_mb = os.path.getsize(mp3_path) / (1024 * 1024)
        if size_mb > 49.0:
            send_rubika_msg(rubika_chat_id, f"📦 حجم فایل ({size_mb:.1f}MB) بیشتر از حد مجاز بله است. در حال فشرده‌سازی...")
            compress_audio(mp3_path, compressed_path, target_size_mb=48)
            final_file = compressed_path

        # ارسال به بله
        send_rubika_msg(rubika_chat_id, "🚀 در حال ارسال فایل صوتی به پیام‌رسان بله...")
        success = send_bale_audio(bale_chat_id, final_file, title=original_name or "Voice/Audio")

        if success:
            send_rubika_msg(rubika_chat_id, "✅ فایل با موفقیت تبدیل و به ربات بله تحویل داده شد.")
        else:
            send_rubika_msg(rubika_chat_id, "❌ ارسال به بله با خطا مواجه شد. بررسی کنید که فایل زیر ۵۰ مگابایت باشد.")

    except Exception as e:
        print(f"Processing error: {e}")
        send_rubika_msg(rubika_chat_id, "❌ خطایی در پردازش فایل رخ داد.")
    finally:
        for p in [raw_path, mp3_path, compressed_path]:
            if os.path.exists(p):
                os.remove(p)

# ==================== حلقه‌های اصلی دریافت پیام ====================
def check_bale_updates(offset):
    try:
        res = requests.get(f"{BALE_API}/getUpdates", params={"offset": offset, "timeout": 2}, timeout=5).json()
        if not res.get("ok"):
            return offset
        for upd in res.get("result", []):
            offset = upd["update_id"] + 1
            msg = upd.get("message", {})
            text = msg.get("text", "").strip()
            chat_id = msg.get("chat", {}).get("id")

            if text == PASSWORD or text == "/start":
                data_store["bale_chat_id"] = chat_id
                save_data(data_store)
                send_bale_msg(chat_id, "✅ اتصال برقرار شد! از این پس تمام فایل‌های ارسالی شما در روبیکا تبدیل شده و در اینجا تحویل داده می‌شوند.")
    except Exception:
        pass
    return offset

def run_bot():
    print("Bot is running...")
    rubika_offset = None
    bale_offset = 0

    while True:
        # ۱. بررسی آپدیت‌های بله (برای دریافت خودکار چت‌آیدی شما)
        bale_offset = check_bale_updates(bale_offset)

        # ۲. بررسی آپدیت‌های روبیکا
        try:
            payload = {"limit": 10}
            if rubika_offset:
                payload["offset_id"] = rubika_offset

            res = requests.post(f"{RUBIKA_API}/getUpdates", json=payload, timeout=10).json()
            updates = res.get("updates", [])
            if res.get("next_offset_id"):
                rubika_offset = res.get("next_offset_id")

            for upd in updates:
                msg = upd.get("new_message", {})
                chat_id = upd.get("chat_id")
                sender_id = msg.get("sender_id")
                text = (msg.get("text") or "").strip()
                file_info = msg.get("file")

                # بررسی رمز عبور
                if text == PASSWORD:
                    if sender_id not in data_store["auth_users"]:
                        data_store["auth_users"].append(sender_id)
                        save_data(data_store)
                    send_rubika_msg(chat_id, "🔓 رمز تایید شد! از این لحظه هر فایل، ویدیو یا صوتی بفرستید، به MP3 تبدیل شده و به بله ارسال می‌شود.")
                    continue

                # بررسی وضعیت لاگین
                if sender_id not in data_store["auth_users"]:
                    if file_info or text:
                        send_rubika_msg(chat_id, "🔒 دسترسی مسدود است. لطفاً ابتدا رمز عبور را ارسال کنید.")
                    continue

                # پردازش فایل در صورت تایید بودن رمز
                if file_info:
                    file_id = file_info.get("file_id")
                    file_name = file_info.get("file_name", "audio.mp3")
                    process_and_forward(file_id, file_name, chat_id)

        except Exception as e:
            print(f"Rubika polling loop error: {e}")

        time.sleep(2)

if __name__ == "__main__":
    run_bot()
