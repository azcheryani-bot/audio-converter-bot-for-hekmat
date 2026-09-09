import os
import sys
import time
import json
import subprocess
import requests

sys.stdout.reconfigure(line_buffering=True)

RUBIKA_TOKEN = "CDJJFE0VZBQFANITRNGPDRBAJXSJBUVJSJOEJFQOJNAKSBFYHCDRPRQRMUVIUBDO"
BALE_TOKEN = "628083238:xSdEBoOooDiIVRPwAfL8eOmxtOqIhGUk4DI"
PASSWORDS = ["313hekmat", "۳۱۳hekmat"]

RUBIKA_API = f"https://botapi.rubika.ir/v3/{RUBIKA_TOKEN}"
BALE_API = f"https://tapi.bale.ai/bot{BALE_TOKEN}"
DATA_FILE = "bot_data.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "*/*"
}

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
    return {"auth_users": [], "bale_chat_id": 270871838}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)

data_store = load_data()

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
            res = requests.post(url, data=data, files=files, timeout=300)
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

def convert_to_mp3(input_path, output_path, target_kbps="96k"):
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vn", "-c:a", "libmp3lame", "-b:a", target_kbps,
        output_path
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        print(f"[FFmpeg Error]: {res.stderr}", flush=True)
        return False
    return True

def compress_audio(input_path, output_path, duration, target_size_mb=47):
    if duration <= 0:
        duration = get_audio_duration(input_path)
    if duration <= 0:
        duration = 3600 # پیش‌فرض یک ساعت
    
    # محاسبه دقیق بیت‌ریت برای رسیدن به زیر ۵۰ مگابایت
    target_bytes = target_size_mb * 1024 * 1024
    bitrate_kbps = int((target_bytes * 8) / duration / 1000)
    bitrate_kbps = max(24, min(bitrate_kbps, 96))
    
    print(f"[Compressing] Target bitrate: {bitrate_kbps}k for duration: {duration}s", flush=True)
    return convert_to_mp3(input_path, output_path, target_kbps=f"{bitrate_kbps}k")

def process_and_forward(file_id, original_name, rubika_chat_id):
    bale_chat_id = data_store.get("bale_chat_id") or 270871838

    send_rubika_msg(rubika_chat_id, "⏳ در حال دانلود فایل از روبیکا...")
    download_url = get_rubika_file_url(file_id)
    if not download_url:
        send_rubika_msg(rubika_chat_id, "❌ دریافت آدرس فایل با خطا مواجه شد.")
        return

    # استخراج پسوند فایل ورودی
    ext = os.path.splitext(original_name)[1]
    if not ext:
        ext = ".m4a"
    raw_path = f"raw_{file_id}{ext}"
    mp3_path = f"out_{file_id}.mp3"
    comp_path = f"comp_{file_id}.mp3"

    try:
        # دانلود با هویت مرورگر
        with requests.get(download_url, headers=HEADERS, stream=True, timeout=180) as r:
            if r.status_code != 200:
                print(f"[Download Blocked] Code: {r.status_code}, Response: {r.text[:200]}", flush=True)
                send_rubika_msg(rubika_chat_id, f"❌ سرور روبیکا اجازه دانلود نداد (کد: {r.status_code})")
                return
            with open(raw_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=32768):
                    if chunk:
                        f.write(chunk)

        downloaded_size = os.path.getsize(raw_path)
        print(f"[Downloaded File]: {raw_path} | Size: {downloaded_size} bytes", flush=True)

        if downloaded_size < 1000:
            send_rubika_msg(rubika_chat_id, "❌ فایل دریافتی ناقص است.")
            return

        send_rubika_msg(rubika_chat_id, "🔄 در حال تبدیل به صوت MP3...")
        
        # بررسی طول صوت
        duration = get_audio_duration(raw_path)
        print(f"[Audio Duration]: {duration} seconds", flush=True)

        # تبدیل با بیت‌ریت استاندارد ۹۶ کیلوبیت
        success = convert_to_mp3(raw_path, mp3_path, target_kbps="96k")
        if not success or not os.path.exists(mp3_path):
            send_rubika_msg(rubika_chat_id, "❌ تبدیل فایل صوتی انجام نشد.")
            return

        final_file = mp3_path
        size_mb = os.path.getsize(mp3_path) / (1024 * 1024)
        print(f"[Converted MP3 Size]: {size_mb:.2f} MB", flush=True)

        # اگر بعد از تبدیل، حجم فایل بالاتر از ۴۸ مگ بود فشرده شود
        if size_mb > 48.0:
            send_rubika_msg(rubika_chat_id, f"📦 حجم فایل ({size_mb:.1f}MB) بیش از حد بله است؛ در حال فشرده‌سازی خودکار...")
            compress_audio(raw_path, comp_path, duration, target_size_mb=46)
            if os.path.exists(comp_path):
                final_file = comp_path

        send_rubika_msg(rubika_chat_id, "🚀 در حال ارسال به بازوی بله...")
        if send_bale_audio(bale_chat_id, final_file, title=original_name or "درس اسفار"):
            send_rubika_msg(rubika_chat_id, "✅ فایل با موفقیت در بله تحویل داده شد!")
        else:
            send_rubika_msg(rubika_chat_id, "❌ خطایی در آپلود نهایی به بله رخ داد.")

    except Exception as e:
        print(f"Error processing: {e}", flush=True)
        send_rubika_msg(rubika_chat_id, f"❌ خطا: {e}")
    finally:
        for p in [raw_path, mp3_path, comp_path]:
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass

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
                    send_bale_msg(cid, "✅ شناسه بله شما بروزرسانی شد.")
    except Exception:
        pass
    return offset

def run_bot():
    print(">>> ربات با تنظیمات جدید راه‌اندازی شد...", flush=True)
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
                msg = upd.get("new_message", {})
                chat_id = upd.get("chat_id")
                sender_id = msg.get("sender_id")
                raw_text = (msg.get("text") or "").strip()
                norm_text = normalize(raw_text)
                file_info = msg.get("file")

                if norm_text == "/start":
                    send_rubika_msg(chat_id, "سلام! لطفاً رمز عبور را بفرستید:")
                    continue

                if norm_text == "313hekmat":
                    if sender_id not in data_store["auth_users"]:
                        data_store["auth_users"].append(sender_id)
                        save_data(data_store)
                    send_rubika_msg(chat_id, "🔓 رمز تایید شد! فایل‌های خود را ارسال کنید.")
                    continue

                if sender_id not in data_store["auth_users"]:
                    send_rubika_msg(chat_id, "🔒 لطفاً ابتدا رمز عبور را ارسال کنید.")
                    continue

                if file_info:
                    file_id = file_info.get("file_id")
                    file_name = file_info.get("file_name", "audio.m4a")
                    process_and_forward(file_id, file_name, chat_id)

        except Exception as e:
            print(f"[Polling Error]: {e}", flush=True)

        time.sleep(2)

if __name__ == "__main__":
    run_bot()
