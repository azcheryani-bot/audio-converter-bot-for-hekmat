import os
import sys
import time
import json
import queue
import threading
import subprocess
import requests

sys.stdout.reconfigure(line_buffering=True)

# ==================== تنظیمات ====================
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

# صف پردازش فایل‌ها (دانلود و تبدیل ترتیبی)
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
    # ساختار: نگهداری مشخصات هر کاربر روبیکا و آیدی بله متناظر آن
    return {"users": {}}

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
    payload = {"chat_id": chat_id, "text": text}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"[Bale Send Error]: {e}", flush=True)

def send_bale_audio(chat_id, file_path, title="صوت"):
    url = f"{BALE_API}/sendAudio"
    try:
        with open(file_path, "rb") as f:
            files = {"audio": (os.path.basename(file_path), f, "audio/mpeg")}
            data = {"chat_id": chat_id, "caption": f"🎵 {title}"}
            res = requests.post(url, data=data, files=files, timeout=300)
            return res.json().get("ok", False)
    except Exception as e:
        print(f"[Bale Audio Error]: {e}", flush=True)
        return False

# ==================== ابزارهای تبدیل و فشرده‌سازی ====================
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

def convert_to_mp3(input_path, output_path, target_kbps="96k"):
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vn", "-c:a", "libmp3lame", "-b:a", target_kbps,
        output_path
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return res.returncode == 0

def compress_audio(input_path, output_path, duration, target_size_mb=47):
    if duration <= 0:
        duration = get_audio_duration(input_path) or 3600
    target_bytes = target_size_mb * 1024 * 1024
    bitrate_kbps = int((target_bytes * 8) / duration / 1000)
    bitrate_kbps = max(24, min(bitrate_kbps, 96))
    return convert_to_mp3(input_path, output_path, target_kbps=f"{bitrate_kbps}k")

# ==================== تسک پردازش فایل ====================
def process_single_task(task):
    file_id = task["file_id"]
    original_name = task["original_name"]
    rubika_chat_id = task["rubika_chat_id"]
    bale_chat_id = task["bale_chat_id"]

    send_rubika_msg(rubika_chat_id, "⏳ دانلود فایل شما آغاز شد...")
    download_url = get_rubika_file_url(file_id)
    if not download_url:
        send_rubika_msg(rubika_chat_id, "❌ خطا در دریافت لینک مستقیم فایل از روبیکا.")
        return

    ext = os.path.splitext(original_name)[1] or ".m4a"
    raw_path = f"raw_{file_id}{ext}"
    mp3_path = f"out_{file_id}.mp3"
    comp_path = f"comp_{file_id}.mp3"

    try:
        with requests.get(download_url, headers=HEADERS, stream=True, timeout=180) as r:
            if r.status_code != 200:
                send_rubika_msg(rubika_chat_id, f"❌ سرور دانلود اجازه نداد ({r.status_code})")
                return
            with open(raw_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=32768):
                    if chunk:
                        f.write(chunk)

        if os.path.getsize(raw_path) < 1000:
            send_rubika_msg(rubika_chat_id, "❌ فایل دریافتی ناقص است.")
            return

        send_rubika_msg(rubika_chat_id, "🔄 در حال تبدیل به فرمت MP3...")
        duration = get_audio_duration(raw_path)
        success = convert_to_mp3(raw_path, mp3_path, target_kbps="96k")
        if not success or not os.path.exists(mp3_path):
            send_rubika_msg(rubika_chat_id, "❌ خطایی در فرایند تبدیل صوت رخ داد.")
            return

        final_file = mp3_path
        size_mb = os.path.getsize(mp3_path) / (1024 * 1024)

        if size_mb > 48.0:
            send_rubika_msg(rubika_chat_id, f"📦 حجم فایل ({size_mb:.1f}MB) بالا بود؛ فشرده‌سازی خودکار انجام شد.")
            compress_audio(raw_path, comp_path, duration, target_size_mb=46)
            if os.path.exists(comp_path):
                final_file = comp_path

        send_rubika_msg(rubika_chat_id, "🚀 در حال تحویل صوت به حساب بله شما...")
        if send_bale_audio(bale_chat_id, final_file, title=original_name or "صوت تبدیل شده"):
            send_rubika_msg(rubika_chat_id, "✅ فایل با موفقیت به بله شما تحویل داده شد!")
        else:
            send_rubika_msg(rubika_chat_id, "❌ خطا در تحویل فایل به بله (اطمینان حاصل کنید ربات بله را مسدود نکرده باشید).")

    except Exception as e:
        print(f"[Task Error]: {e}", flush=True)
        send_rubika_msg(rubika_chat_id, f"❌ خطا در پردازش: {e}")
    finally:
        for p in [raw_path, mp3_path, comp_path]:
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass

# حلقه پس‌زمینه برای خواندن نوبتی صف
def queue_worker():
    while True:
        task = task_queue.get()
        try:
            process_single_task(task)
        except Exception as e:
            print(f"[Worker Exception]: {e}", flush=True)
        finally:
            task_queue.task_done()

# ==================== دریافت پیام‌های بله ====================
def check_bale_updates(offset):
    try:
        res = requests.get(f"{BALE_API}/getUpdates", params={"offset": offset, "timeout": 1}, timeout=4).json()
        if res.get("ok"):
            for upd in res.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message", {})
                cid = msg.get("chat", {}).get("id")
                first_name = msg.get("from", {}).get("first_name", "کاربر")
                
                # ارسال شناسه بله به کاربر برای ثبت در روبیکا
                if cid:
                    welcome_text = (
                        f"سلام {first_name} عزیز!\n"
                        f"شناسه بله (Chat ID) اختصاصی شما:\n\n"
                        f"👉 `{cid}` 👈\n\n"
                        f"این عدد را کپی کنید و در ربات روبیکا بفرستید تا حساب‌ها متصل شوند."
                    )
                    send_bale_msg(cid, welcome_text)
    except Exception:
        pass
    return offset

# ==================== موتور اصلی ربات ====================
def run_bot():
    print(">>> ربات با سیستم صف و تفکیک کاربر فعال شد...", flush=True)
    
    # راه‌اندازی کارگر پردازش در یک Thread مجزا
    worker_thread = threading.Thread(target=queue_worker, daemon=True)
    worker_thread.start()

    rubika_offset = None
    bale_offset = 0

    while True:
        bale_offset = check_bale_updates(bale_offset)

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

                # جلوگیری از پردازش پیام‌های تکراری
                if msg_id:
                    if msg_id in processed_messages:
                        continue
                    processed_messages.add(msg_id)
                    if len(processed_messages) > 1000:
                        processed_messages.clear()

                chat_id = upd.get("chat_id")
                sender_id = msg.get("sender_id")
                raw_text = (msg.get("text") or "").strip()
                norm_text = normalize(raw_text)
                file_info = msg.get("file")

                # دریافت اطلاعات کاربر در دیتابیس
                user_info = data_store["users"].get(str(sender_id), {"auth": False, "bale_id": None})

                # شناسه کاربری قبلی شما به صورت پیش‌فرض برای سهولت
                if str(sender_id) not in data_store["users"] and norm_text in ["313hekmat", "۳۱۳hekmat"]:
                    user_info["bale_id"] = 270871838

                # ۱. دستور استارت
                if norm_text == "/start":
                    send_rubika_msg(chat_id, "سلام! خوش آمدید.\nلطفاً رمز عبور را ارسال کنید:")
                    continue

                # ۲. ارسال رمز عبور
                if "313hekmat" in norm_text:
                    user_info["auth"] = True
                    data_store["users"][str(sender_id)] = user_info
                    save_data(data_store)
                    
                    if user_info.get("bale_id"):
                        send_rubika_msg(chat_id, f"🔓 رمز تایید شد!\nحساب بله شما روی ({user_info['bale_id']}) تنظیم است.\nاکنون هر فایلی بفرستید در صف تبدیل قرار گرفته و به بله شما ارسال می‌شود.")
                    else:
                        send_rubika_msg(chat_id, "🔓 رمز تایید شد!\nحالا لطفاً شناسه بله (Chat ID) خود را بفرستید.\n(برای دریافت شناسه، در ربات بله پیام /start را بزنید).")
                    continue

                # ۳. ارسال شناسه عددی بله
                if norm_text.isdigit() and len(norm_text) >= 6:
                    if not user_info.get("auth"):
                        send_rubika_msg(chat_id, "🔒 لطفاً ابتدا رمز عبور را ارسال کنید.")
                        continue
                    user_info["bale_id"] = int(norm_text)
                    data_store["users"][str(sender_id)] = user_info
                    save_data(data_store)
                    send_rubika_msg(chat_id, f"✅ حساب بله شما با موفقیت روی شناسه {norm_text} متصل شد!\nاز حالا هر فایلی بفرستید فقط به بله شخص شما ارسال خواهد شد.")
                    continue

                # ۴. بررسی قفل بودن دسترسی
                if not user_info.get("auth"):
                    if file_info or norm_text:
                        send_rubika_msg(chat_id, "🔒 دسترسی مسدود است. لطفاً ابتدا رمز عبور را بفرستید.")
                    continue

                # ۵. بررسی متصل بودن حساب بله
                if not user_info.get("bale_id"):
                    send_rubika_msg(chat_id, "⚠️ شما هنوز شناسه بله خود را وارد نکرده‌اید!\nلطفاً وارد ربات بله شده، دستور /start را بزنید و عدد دریافتی را اینجا ارسال کنید.")
                    continue

                # ۶. دریافت فایل و قرار دادن در صف نوبت
                if file_info:
                    file_id = file_info.get("file_id")
                    file_name = file_info.get("file_name", "audio.m4a")
                    
                    # ثبت تسک در صف پردازش
                    task_queue.put({
                        "file_id": file_id,
                        "original_name": file_name,
                        "rubika_chat_id": chat_id,
                        "bale_chat_id": user_info["bale_id"]
                    })
                    
                    q_size = task_queue.qsize()
                    send_rubika_msg(chat_id, f"📥 فایل شما در صف پردازش قرار گرفت (نوبت شما: {q_size})\nبه محض آماده شدن ارسال خواهد شد.")

        except Exception as e:
            print(f"[Polling Loop Error]: {e}", flush=True)

        time.sleep(1.5)

if __name__ == "__main__":
    run_bot()
