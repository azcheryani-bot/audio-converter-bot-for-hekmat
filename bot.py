import os
import sys
import time
import json
import queue
import shutil
import threading
import subprocess
import urllib3
import requests

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
sys.stdout.reconfigure(line_buffering=True)

BOT_START_TIME = int(time.time())

# ==================== تنظیمات ====================
RUBIKA_TOKEN = os.environ.get("RUBIKA_TOKEN")
BALE_TOKEN = os.environ.get("BALE_TOKEN")
BOT_PASSWORD = os.environ.get("BOT_PASSWORD")
RUBIKA_API = f"https://botapi.rubika.ir/v3/{RUBIKA_TOKEN}"
BALE_API = f"https://tapi.bale.ai/bot{BALE_TOKEN}"
DATA_FILE = "bot_data.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Encoding": "identity",  # جلوگیری از باگ فشرده‌سازی خالی در CDN روبیکا
    "Connection": "keep-alive"
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
    return {
        "users": {
            "b0FagL0BD4g0710d9982da4250b599f9": {"auth": True, "bale_id": 270871838}
        }
    }

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)

data_store = load_data()

# ==================== روبیکا ====================
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
        print(f"[Rubika getFile Response]: {res}", flush=True)
        data_obj = res.get("data", {}) if isinstance(res.get("data"), dict) else res
        dl_url = (
            data_obj.get("download_url")
            or res.get("download_url")
            or data_obj.get("file_url")
            or data_obj.get("url")
        )
        return str(dl_url).strip() if dl_url else None
    except Exception as e:
        print(f"[Rubika File Error]: {e}", flush=True)
        return None

# ==================== بله ====================
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
            res = requests.post(url, data=data, files=files, timeout=180)
            res_json = res.json()
            print(f"[Bale Response]: {res_json}", flush=True)
            return res_json.get("ok", False)
    except Exception as e:
        print(f"[Bale Upload Exception]: {e}", flush=True)
        return False

# ==================== دانلودر ۳ لایه ضد باگ ۰ بایت ====================
def download_file_robust(url, dest_path, file_id=None, timeout=180):
    """
    تلاش برای دانلود فایل با ۳ روش مختلف (Requests Session, Curl, Wget/FFmpeg)
    تا مشکل ۰ بایت شدن فایل‌های روبیکا به طور کامل برطرف شود.
    """
    # --- روش اول: استفاده از curl سیستم‌عامل (بهترین روش برای دور زدن محدودیت CDN روبیکا) ---
    if shutil.which("curl"):
        try:
            cmd_curl = [
                "curl", "-L", "-k", "-s", "--retry", "3", "--retry-delay", "2",
                "--max-time", str(timeout),
                "-A", HEADERS["User-Agent"],
                "-o", dest_path,
                url
            ]
            subprocess.run(cmd_curl, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout + 10)
            if os.path.exists(dest_path) and os.path.getsize(dest_path) > 100:
                print(f"[Download Success via CURL]: {os.path.getsize(dest_path)} bytes", flush=True)
                return True, "OK (curl)"
        except Exception as e:
            print(f"[Curl Download Error]: {e}", flush=True)

    # --- روش دوم: دانلود با Requests (تلاش مجدد با گرفتن لینک تازه در صورت منقضی بودن) ---
    for attempt in range(2):
        current_url = get_rubika_file_url(file_id) if (attempt > 0 and file_id) else url
        if not current_url:
            current_url = url
        try:
            session = requests.Session()
            r = session.get(current_url, headers=HEADERS, stream=True, timeout=timeout, verify=False, allow_redirects=True)
            print(f"[Requests Attempt {attempt+1}] Status: {r.status_code}, Headers: {dict(r.headers)}", flush=True)

            if r.status_code in (200, 206):
                with open(dest_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=32768):
                        if chunk:
                            f.write(chunk)

                if os.path.exists(dest_path) and os.path.getsize(dest_path) > 100:
                    print(f"[Download Success via Requests]: {os.path.getsize(dest_path)} bytes", flush=True)
                    return True, "OK (requests)"

            # تلاش بدون stream=True (مستقیم r.content)
            r_direct = session.get(current_url, headers=HEADERS, stream=False, timeout=timeout, verify=False)
            if r_direct.content and len(r_direct.content) > 100:
                with open(dest_path, "wb") as f:
                    f.write(r_direct.content)
                print(f"[Download Success via Direct Content]: {len(r_direct.content)} bytes", flush=True)
                return True, "OK (direct)"

        except Exception as e:
            print(f"[Requests Exception Attempt {attempt+1}]: {e}", flush=True)
            if os.path.exists(dest_path) and os.path.getsize(dest_path) > 512:
                return True, "Partial Saved"

        time.sleep(1)

    return False, "سرور روبیکا ۰ بایت داده برگرداند"

# ==================== موتور جراحی و بازیابی عمیق صوت ====================
def is_valid_output(path):
    return os.path.exists(path) and os.path.getsize(path) > 512

def carve_clean_stream(raw_path, carved_path):
    """جداسازی بخش صوتی از فایل‌های دارای هدر سوخته یا فاقد جدول moov"""
    try:
        with open(raw_path, "rb") as f:
            data = f.read()

        if len(data) < 64:
            return False

        # ۱. اگر فایل M4A/MP4 است که جدول moov ندارد، از داخل بلوک mdat ببریم
        mdat_pos = data.find(b"mdat")
        if mdat_pos != -1 and mdat_pos + 4 < len(data):
            with open(carved_path, "wb") as f:
                f.write(data[mdat_pos + 4:])
            return True

        # ۲. حذف بایت‌های صفر ابتدایی
        stripped = data.lstrip(b"\x00")
        if len(stripped) < len(data) and len(stripped) > 128:
            with open(carved_path, "wb") as f:
                f.write(stripped)
            return True

        # ۳. جستجوی اولین فریم Ogg یا ADTS/MP3
        ogg_pos = data.find(b"OggS")
        if ogg_pos > 0:
            with open(carved_path, "wb") as f:
                f.write(data[ogg_pos:])
            return True

        for i in range(min(len(data) - 2, 65536)):
            if data[i] == 0xFF and (data[i + 1] & 0xF0) in (0xF0, 0xE0):
                if i > 0:
                    with open(carved_path, "wb") as f:
                        f.write(data[i:])
                    return True
                break

        return False
    except Exception as e:
        print(f"[Carve Error]: {e}", flush=True)
        return False

def run_ffmpeg(args, input_bytes=None):
    try:
        res = subprocess.run(
            args,
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=180
        )
        return res.returncode == 0, res.stderr.decode("utf-8", errors="ignore")
    except Exception as e:
        return False, str(e)

def repair_and_convert_to_mp3(input_source, output_path, target_kbps="24k", is_url=False):
    """
    تعمیر فایل صوتی (چه فایل لوکال باشد چه مستقیماً لینک اینترنتی) و تبدیل به MP3
    """
    base_encode = ["-vn", "-c:a", "libmp3lame", "-ac", "1", "-ar", "24000", "-b:a", target_kbps, output_path]

    # --- مرحله ۱: تبدیل استاندارد با نادیده گرفتن فریم‌های خراب ---
    cmd1 = [
        "ffmpeg", "-y",
        "-fflags", "+discardcorrupt+genpts+igndts",
        "-err_detect", "ignore_err",
        "-i", input_source,
        "-af", "aresample=async=1:min_hard_comp=0.100000:first_pts=0"
    ] + base_encode
    ok, err = run_ffmpeg(cmd1)
    if is_valid_output(output_path):
        print("[Repair] Stage 1 Success", flush=True)
        return True
    print(f"[Stage 1 Log]: {err[-250:]}", flush=True)

    if is_url:
        return False

    # --- مرحله ۲: ارسال فایل از طریق Pipe (دور زدن خطای ساختار کانتینر) ---
    try:
        with open(input_source, "rb") as f:
            raw_bytes = f.read()

        cmd2 = [
            "ffmpeg", "-y",
            "-fflags", "+discardcorrupt+genpts+igndts",
            "-err_detect", "ignore_err",
            "-i", "pipe:0"
        ] + base_encode
        run_ffmpeg(cmd2, input_bytes=raw_bytes)
        if is_valid_output(output_path):
            print("[Repair] Stage 2 Success (Pipe Stream)", flush=True)
            return True
    except Exception as e:
        print(f"[Stage 2 Exception]: {e}", flush=True)

    # --- مرحله ۳: جراحی بایت‌ها و تلاش مجدد ---
    carved_path = input_source + ".carved"
    try:
        if carve_clean_stream(input_source, carved_path):
            for fmt in [None, "aac", "mp3", "ogg", "amr", "flac", "wav"]:
                cmd3 = ["ffmpeg", "-y", "-fflags", "+discardcorrupt+genpts", "-err_detect", "ignore_err"]
                if fmt:
                    cmd3 += ["-f", fmt]
                cmd3 += ["-i", carved_path] + base_encode
                run_ffmpeg(cmd3)
                if is_valid_output(output_path):
                    print(f"[Repair] Stage 3 Success (Carved as {fmt or 'auto'})", flush=True)
                    return True
    finally:
        if os.path.exists(carved_path):
            try:
                os.remove(carved_path)
            except Exception:
                pass

    # --- مرحله ۴: اجبار دیموکسر روی فایل اصلی ---
    for fmt in ["mp3", "aac", "ogg", "amr", "wav", "flac", "m4a"]:
        cmd4 = [
            "ffmpeg", "-y",
            "-fflags", "+discardcorrupt+genpts+igndts",
            "-err_detect", "ignore_err",
            "-f", fmt,
            "-i", input_source
        ] + base_encode
        run_ffmpeg(cmd4)
        if is_valid_output(output_path):
            print(f"[Repair] Stage 4 Success (Forced Format: {fmt})", flush=True)
            return True

    # --- مرحله ۵: بازیابی اضطراری به عنوان موج خام (Raw PCM Rescue) ---
    cmd5 = [
        "ffmpeg", "-y",
        "-f", "s16le",
        "-ar", "16000",
        "-ac", "1",
        "-i", input_source,
        "-af", "highpass=f=120,lowpass=f=3800,volume=0.5"
    ] + base_encode
    run_ffmpeg(cmd5)
    if is_valid_output(output_path):
        print("[Repair] Stage 5 Success (Emergency Raw PCM)", flush=True)
        return True

    return False

# ==================== پردازش فایل ====================
def process_single_task(task):
    file_id = task["file_id"]
    original_name = task["original_name"]
    rubika_chat_id = task["rubika_chat_id"]
    bale_chat_id = task["bale_chat_id"]

    send_rubika_msg(rubika_chat_id, "⏳ دریافت لینک و دانلود فایل از روبیکا آغاز شد...")
    download_url = get_rubika_file_url(file_id)
    if not download_url:
        send_rubika_msg(rubika_chat_id, "❌ دریافت آدرس دانلود فایل از سرور روبیکا ناموفق بود.")
        return

    ext = os.path.splitext(original_name)[1] or ".m4a"
    raw_path = f"raw_{file_id}{ext}"
    mp3_path = f"out_{file_id}.mp3"
    light_path = f"light_{file_id}.mp3"

    try:
        success_download, msg = download_file_robust(download_url, raw_path, file_id=file_id, timeout=180)

        converted = False
        if success_download:
            raw_size_kb = os.path.getsize(raw_path) / 1024
            send_rubika_msg(rubika_chat_id, f"🛠 فایل دانلود شد ({raw_size_kb:.1f} KB). در حال تعمیر و تبدیل به MP3...")
            converted = repair_and_convert_to_mp3(raw_path, mp3_path, target_kbps="24k", is_url=False)
        else:
            # لایه سوم: اگر دانلودر پایتون/curl فایل را نگرفت، لینک را مستقیماً به FFmpeg می‌دهیم
            send_rubika_msg(rubika_chat_id, "⚡ در حال استریم و تعمیر مستقیم فایل از لینک سرور...")
            fresh_url = get_rubika_file_url(file_id) or download_url
            converted = repair_and_convert_to_mp3(fresh_url, mp3_path, target_kbps="24k", is_url=True)

        if not converted or not is_valid_output(mp3_path):
            send_rubika_msg(rubika_chat_id, f"❌ سرور روبیکا فایل را خالی ارسال کرد ({msg}). لطفاً فایل را مجدداً فوروارد کنید.")
            return

        size_mb = os.path.getsize(mp3_path) / (1024 * 1024)
        print(f"[Repaired MP3 Size]: {size_mb:.2f} MB", flush=True)

        clean_title = os.path.splitext(original_name)[0] if original_name else "صوت"
        send_rubika_msg(rubika_chat_id, f"🚀 صوت آماده شد (حجم: {size_mb:.2f}MB). در حال ارسال به بله...")

        if send_bale_audio(bale_chat_id, mp3_path, title=clean_title):
            send_rubika_msg(rubika_chat_id, "✅ فایل با موفقیت تعمیر و به بله تحویل داده شد!")
        else:
            print("[Retrying with 16k ultra-light]...", flush=True)
            source_for_light = raw_path if os.path.exists(raw_path) and os.path.getsize(raw_path) > 100 else mp3_path
            if repair_and_convert_to_mp3(source_for_light, light_path, target_kbps="16k") and send_bale_audio(bale_chat_id, light_path, title=clean_title):
                send_rubika_msg(rubika_chat_id, "✅ فایل با موفقیت به بله تحویل داده شد!")
            else:
                send_rubika_msg(rubika_chat_id, "❌ خطای شبکه بله در تحویل فایل.")

    except Exception as e:
        print(f"[Process Error]: {e}", flush=True)
        send_rubika_msg(rubika_chat_id, f"❌ خطا: {e}")
    finally:
        for p in [raw_path, mp3_path, light_path]:
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

# ==================== دریافت پیام‌های بله ====================
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
                            f"این عدد را در روبیکا بفرستید تا حساب متصل شود."
                        )
                        send_bale_msg(cid, text)
        except Exception:
            pass
        time.sleep(3)

# ==================== حلقه اصلی روبیکا ====================
def run_bot():
    print(">>> ربات با دانلودر ۳ لایه و موتور ترمیم صوت فعال شد...", flush=True)

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

                file_info = msg.get("file") or msg.get("voice") or msg.get("audio") or msg.get("music")

                user = data_store["users"].get(sender_id, {"auth": False, "bale_id": None})

                if norm_text == "/start":
                    send_rubika_msg(chat_id, "سلام! لطفاً رمز عبور را ارسال کنید:")
                    continue

                if norm_text == BOT_PASSWORD:
                    user["auth"] = True
                    data_store["users"][sender_id] = user
                    save_data(data_store)

                    if user.get("bale_id"):
                        send_rubika_msg(chat_id, f"🔓 رمز تایید شد!\nحساب بله متصل: ({user['bale_id']})\nفایل‌های ارسالی مستقیماً تعمیر و تحویل داده می‌شوند.")
                    else:
                        send_rubika_msg(chat_id, "🔓 رمز تایید شد!\nحالا شناسه بله (Chat ID) خود را بفرستید.")
                    continue

                if norm_text.isdigit() and len(norm_text) >= 6:
                    if not user.get("auth"):
                        send_rubika_msg(chat_id, "🔒 لطفاً ابتدا رمز عبور را ارسال کنید.")
                        continue
                    user["bale_id"] = int(norm_text)
                    data_store["users"][sender_id] = user
                    save_data(data_store)
                    send_rubika_msg(chat_id, f"✅ حساب بله روی {norm_text} تنظیم شد.")
                    continue

                if not user.get("auth"):
                    if file_info or norm_text:
                        send_rubika_msg(chat_id, "🔒 دسترسی مسدود است. رمز عبور را وارد کنید.")
                    continue

                if not user.get("bale_id"):
                    send_rubika_msg(chat_id, "⚠️ شناسه بله خود را ارسال نکرده‌اید.")
                    continue

                if file_info:
                    file_id = file_info.get("file_id")
                    file_name = file_info.get("file_name") or "voice.m4a"

                    task_queue.put({
                        "file_id": file_id,
                        "original_name": file_name,
                        "rubika_chat_id": chat_id,
                        "bale_chat_id": user["bale_id"]
                    })
                    send_rubika_msg(chat_id, f"📥 فایل دریافت شد و در صف قرار گرفت (نوبت: {task_queue.qsize()})")

        except Exception as e:
            print(f"[Polling Error]: {e}", flush=True)

        time.sleep(2)

if __name__ == "__main__":
    run_bot()
