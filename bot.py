import os
import sys
import time
import json
import queue
import threading
import subprocess
import requests

sys.stdout.reconfigure(line_buffering=True)

BOT_START_TIME = int(time.time())

# ==================== توکن‌ها و تنظیمات ====================
RUBIKA_TOKEN = "CDJJFE0VZBQFANITRNGPDRBAJXSJBUVJSJOEJFQOJNAKSBFYHCDRPRQRMUVIUBDO"
BALE_TOKEN = "628083238:xSdEBoOooDiIVRPwAfL8eOmxtOqIhGUk4DI"
PASSWORD = "313hekmat"

RUBIKA_API = f"https://botapi.rubika.ir/v3/{RUBIKA_TOKEN}"
BALE_API = f"https://tapi.bale.ai/bot{BALE_TOKEN}"
DATA_FILE = "bot_data.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "*/*"
}

task_queue = queue.Queue()
processed_messages = set()

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
    # شناسه شما از ابتدا ثبت شده است
    return {
        "users": {
            "b0FagL0BD4g0710d9982da4250b599f9": {"auth": True, "bale_id": 270871838}
        }
    }

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)

data_store = load_data()

# ==================== ارتباط با روبیکا ====================
def send_rubika_msg(chat_id, text):
    url = f"{RUBIKA_API}/sendMessage"
    payload = {"chat_id": str(chat_id), "text": text}
    try:
        requests.post(url, json=payload, headers=HEADERS, timeout=10)
    except Exception as e:
        print(f"[Rubika Send Error]: {e}", flush=True)

def get_rubika_file_url(file_id):
    url = f"{RUBIKA_API}/getFile"
    payload = {"file_id": str(file_id)}
    try:
        res = requests.post(url, json=payload, headers=HEADERS, timeout=15).json()
        data_obj = res.get("data", {}) if isinstance(res.get("data"), dict) else res
        return data_obj.get("download_url") or res.get("download_url")
    except Exception as e:
        print(f"[Rubika File Error]: {e}", flush=True)
        return None

# ==================== ارتباط با بله ====================
def send_bale_msg(chat_id, text):
    url = f"{BALE_API}/sendMessage"
    payload = {"chat_id": str(chat_id), "text": text}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"[Bale Send Error]: {e}", flush=True)

def send_bale_audio(chat_id, file_path, title="صوت"):
    url = f"{BALE_API}/sendAudio"
    try:
        size_mb = os.path.getsize(file_path) / (1024 * 1024)
        print(f"[Bale Uploading] To {chat_id}: {file_path} ({size_mb:.2f} MB)...", flush=True)

        with open(file_path, "rb") as f:
            files = {"audio": (os.path.basename(file_path), f, "audio/mpeg")}
            data = {"chat_id": str(chat_id), "caption": f"🎵 {title}"}
            res = requests.post(url, data=data, files=files, timeout=240)
            res_json = res.json()
            print(f"[Bale Response]: {res_json}", flush=True)
            return res_json.get("ok", False)
    except Exception as e:
        print(f"[Bale Upload Exception]: {e}", flush=True)
        return False

# ==================== تبدیل فایل صوتی ====================
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

def convert_to_mp3(input_path, output_path, target_kbps="48k"):
    # مونو (ac 1) حجم فایل گفتاری را نصف می‌کند و کیفیت عالی می‌ماند
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vn", "-c:a", "libmp3lame", "-ac", "1", "-b:a", target_kbps,
        output_path
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return res.returncode == 0

def compress_audio(input_path, output_path, duration, target_size_mb=30):
    if duration <= 0:
        duration = get_audio_duration(input_path) or 3600
    target_bytes = target_size_mb * 1024 * 1024
    bitrate_kbps = int((target_bytes * 8) / duration / 1000)
    bitrate_kbps = max(24, min(bitrate_kbps, 48))
    return convert_to_mp3(input_path, output_path, target_kbps=f"{bitrate_kbps}k")

# ==================== کارگر پردازش نوبتی صف ====================
def process_single_task(task):
    file_id = task["file_id"]
    original_name = task["original_name"]
    rubika_chat_id = task["rubika_chat_id"]
    bale_chat_id = task["bale_chat_id"]

    send_rubika_msg(rubika_chat_id, "⏳ دانلود فایل شما آغاز شد...")
    download_url = get_rubika_file_url(file_id)
    if not download_url:
        send_rubika_msg(rubika_chat_id, "❌ دریافت لینک دانلود از روبیکا ناموفق بود.")
        return

    ext = os.path.splitext(original_name)[1] or ".m4a"
    raw_path = f"raw_{file_id}{ext}"
    mp3_path = f"out_{file_id}.mp3"
    comp_path = f"comp_{file_id}.mp3"

    try:
        with requests.get(download_url, headers=HEADERS, stream=True, timeout=180) as r:
            if r.status_code != 200:
                send_rubika_msg(rubika_chat_id, f"❌ سرور دانلود با خطا مواجه شد ({r.status_code})")
                return
            with open(raw_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=32768):
                    if chunk:
                        f.write(chunk)

        if os.path.getsize(raw_path) < 1000:
            send_rubika_msg(rubika_chat_id, "❌ فایل دانلودشده ناقص است.")
            return

        send_rubika_msg(rubika_chat_id, "🔄 در حال بهینه‌سازی و تبدیل به MP3...")
        duration = get_audio_duration(raw_path)
        success = convert_to_mp3(raw_path, mp3_path, target_kbps="48k")
        if not success or not os.path.exists(mp3_path):
            send_rubika_msg(rubika_chat_id, "❌ خطا در تبدیل به MP3.")
            return

        final_file = mp3_path
        size_mb = os.path.getsize(mp3_path) / (1024 * 1024)

        if size_mb > 45.0:
            send_rubika_msg(rubika_chat_id, f"📦 حجم ({size_mb:.1f}MB) بیش از حد بله است؛ در حال فشرده‌سازی خودکار...")
            compress_audio(raw_path, comp_path, duration, target_size_mb=35)
            if os.path.exists(comp_path):
                final_file = comp_path

        send_rubika_msg(rubika_chat_id, "🚀 در حال تحویل صوت به بله شما...")
        if send_bale_audio(bale_chat_id, final_file, title=original_name or "صوت"):
            send_rubika_msg(rubika_chat_id, "✅ فایل با موفقیت به بله شما تحویل داده شد!")
        else:
            send_rubika_msg(rubika_chat_id, "❌ آپلود به بله ناموفق بود. بررسی کنید که ربات بله را نبسته باشید.")

    except Exception as e:
        print(f"[Process Error]: {e}", flush=True)
        send_rubika_msg(rubika_chat_id, f"❌ خطا: {e}")
    finally:
        for p in [raw_path, mp3_path, comp_path]:
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass

def queue_worker():
    while True:
        task = task_queue.get()
        try:
            process_single_task(task)
        except Exception as e:
            print(f"[Worker Error]: {e}", flush=True)
        finally:
            task_queue.task_done()

# ==================== دریافت پیام‌های بله (برای راهنمایی شناسه) ====================
def bale_listener():
    offset = 0
    while True:
        try:
            res = requests.get(f"{BALE_API}/getUpdates", params={"offset": offset, "timeout": 5}, timeout=10).json()
            if res.get("ok"):
                for upd in res.get("result", []):
                    offset = upd["update_id"] + 1
                    msg = upd.get("message", {})
                    cid = msg.get("chat", {}).get("id")
                    name = msg.get("from", {}).get("first_name", "کاربر")
                    if cid:
                        text = (
                            f"سلام {name} عزیز!\n"
                            f"شناسه بله (Chat ID) شما:\n\n"
                            f"👉 `{cid}` 👈\n\n"
                            f"این عدد را کپی کنید و در ربات روبیکا بفرستید تا فایل‌های شما به این اکانت ارسال شوند."
                        )
                        send_bale_msg(cid, text)
        except Exception:
            pass
        time.sleep(3)

# ==================== حلقه اصلی روبیکا ====================
def run_bot():
    print(">>> ربات چندکاربره فعال شد...", flush=True)

    # راه‌اندازی ورکر صف و شنود بله در ترد‌های جداگانه
    threading.Thread(target=queue_worker, daemon=True).start()
    threading.Thread(target=bale_listener, daemon=True).start()

    rubika_offset = None

    while True:
        try:
            payload = {"limit": 10}
            if rubika_offset:
                payload["offset_id"] = str(rubika_offset)

            r = requests.post(f"{RUBIKA_API}/getUpdates", json=payload, headers=HEADERS, timeout=10)
            res = r.json()

            data_obj = res.get("data", {}) if isinstance(res.get("data"), dict) else res
            updates = data_obj.get("updates") or []
            if data_obj.get("next_offset_id"):
                rubika_offset = data_obj.get("next_offset_id")

            for item in updates:
                upd = item.get("update", item)
                msg = upd.get("new_message") or upd.get("updated_message") or {}
                msg_id = msg.get("message_id")

                # رد کردن پیام‌های قدیمی
                msg_time = int(msg.get("time") or 0)
                if msg_time > 0 and msg_time < BOT_START_TIME:
                    continue

                if msg_id:
                    if msg_id in processed_messages:
                        continue
                    processed_messages.add(msg_id)
                    if len(processed_messages) > 2000:
                        processed_messages.clear()

                chat_id = upd.get("chat_id")
                sender_id = str(msg.get("sender_id"))
                raw_text = (msg.get("text") or "").strip()
                norm_text = normalize(raw_text)
                file_info = msg.get("file")

                # دریافت پروفایل کاربر
                user = data_store["users"].get(sender_id, {"auth": False, "bale_id": None})

                # ۱. استارت
                if norm_text == "/start":
                    send_rubika_msg(chat_id, "سلام! خوش آمدید.\nلطفاً برای فعال‌سازی رمز عبور را ارسال کنید:")
                    continue

                # ۲. ورود رمز عبور
                if "313hekmat" in norm_text:
                    user["auth"] = True
                    data_store["users"][sender_id] = user
                    save_data(data_store)
                    
                    if user.get("bale_id"):
                        send_rubika_msg(chat_id, f"🔓 رمز تایید شد!\nحساب بله شما: ({user['bale_id']})\nهر فایلی بفرستید تبدیل و به بله ارسال می‌شود.")
                    else:
                        send_rubika_msg(chat_id, "🔓 رمز تایید شد!\nحالا لطفاً شناسه بله (Chat ID) خود را بفرستید.\n(برای گرفتن شناسه، در پیام‌رسان بله به ربات یک پیام یا /start بدهید).")
                    continue

                # ۳. تنظیم شناسه عددی بله توسط هر کاربر
                if norm_text.isdigit() and len(norm_text) >= 6:
                    if not user.get("auth"):
                        send_rubika_msg(chat_id, "🔒 لطفاً ابتدا رمز عبور را ارسال کنید.")
                        continue
                    user["bale_id"] = int(norm_text)
                    data_store["users"][sender_id] = user
                    save_data(data_store)
                    send_rubika_msg(chat_id, f"✅ حساب بله شما روی {norm_text} متصل شد!\nاز حالا هر فایلی بفرستید مستقیماً در بله به خود شما تحویل داده می‌شود.")
                    continue

                # بررسی دسترسی
                if not user.get("auth"):
                    if file_info or norm_text:
                        send_rubika_msg(chat_id, "🔒 دسترسی مسدود است. لطفاً ابتدا رمز عبور را ارسال کنید.")
                    continue

                if not user.get("bale_id"):
                    send_rubika_msg(chat_id, "⚠️ لطفاً شناسه بله خود را بفرستید تا بدانیم فایل به کدام حساب بله تحویل داده شود.")
                    continue

                # ۴. دریافت فایل و قرارگیری در صف
                if file_info:
                    file_id = file_info.get("file_id")
                    file_name = file_info.get("file_name", "audio.m4a")
                    
                    task_queue.put({
                        "file_id": file_id,
                        "original_name": file_name,
                        "rubika_chat_id": chat_id,
                        "bale_chat_id": user["bale_id"]
                    })
                    
                    send_rubika_msg(chat_id, f"📥 فایل در صف پردازش قرار گرفت (نوبت شما: {task_queue.qsize()})")

        except Exception as e:
            print(f"[Rubika Polling Error]: {e}", flush=True)

        time.sleep(2)

if __name__ == "__main__":
    run_bot()
