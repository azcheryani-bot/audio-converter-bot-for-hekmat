import os
import sys
import time
import json
import queue
import struct
import shutil
import ctypes
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
    "User-Agent": "Mozilla/5.0 (Linux; Android 13; SM-G990E) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "Accept": "*/*",
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

# ==================== آماده‌سازی ابزارهای سطح پایین ترمیم M4A ====================
FAAD_BIN_PATH = "./faad_mdat_rescue"

C_RESCUE_SOURCE = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <dlfcn.h>

typedef void* NeAACDecHandle;
typedef struct {
    unsigned long bytesconsumed;
    unsigned long samples;
    unsigned char channels;
    unsigned char error;
    unsigned long samplerate;
    unsigned char sbr;
    unsigned char object_type;
    unsigned char header_type;
    unsigned char num_front_channels;
    unsigned char num_side_channels;
    unsigned char num_back_channels;
    unsigned char num_lfe_channels;
    unsigned char channel_position[64];
    unsigned char ps;
    unsigned char pad[128];
} NeAACDecFrameInfo;

typedef NeAACDecHandle (*fn_Open)(void);
typedef char (*fn_Init2)(NeAACDecHandle, unsigned char*, unsigned long, unsigned long*, unsigned char*);
typedef void (*fn_Reset)(NeAACDecHandle, long);
typedef void* (*fn_Decode)(NeAACDecHandle, NeAACDecFrameInfo*, unsigned char*, unsigned long);
typedef void (*fn_Close)(NeAACDecHandle);

static void write_adts(FILE *out, int sr_idx, int ch_cfg, int frame_len) {
    int full_len = frame_len + 7;
    int profile = 1; // AAC-LC
    unsigned char hdr[7];
    hdr[0] = 0xFF;
    hdr[1] = 0xF1;
    hdr[2] = ((profile & 0x03) << 6) | ((sr_idx & 0x0F) << 2) | ((ch_cfg >> 2) & 0x01);
    hdr[3] = ((ch_cfg & 0x03) << 6) | ((full_len >> 11) & 0x03);
    hdr[4] = (full_len >> 3) & 0xFF;
    hdr[5] = ((full_len & 0x07) << 5) | 0x1F;
    hdr[6] = 0xFC;
    fwrite(hdr, 1, 7, out);
}

int main(int argc, char **argv) {
    if (argc < 3) return 1;
    const char *in_path = argv[1];
    const char *out_path = argv[2];
    int hint_sr_idx = (argc >= 4) ? atoi(argv[3]) : -1;
    int hint_ch = (argc >= 5) ? atoi(argv[4]) : -1;

    void *lib = dlopen("libfaad.so.2", RTLD_LAZY);
    if (!lib) lib = dlopen("libfaad.so", RTLD_LAZY);
    if (!lib) return 2;

    fn_Open pOpen = (fn_Open)dlsym(lib, "NeAACDecOpen");
    fn_Init2 pInit2 = (fn_Init2)dlsym(lib, "NeAACDecInit2");
    fn_Reset pReset = (fn_Reset)dlsym(lib, "NeAACDecPostSeekReset");
    fn_Decode pDecode = (fn_Decode)dlsym(lib, "NeAACDecDecode");
    fn_Close pClose = (fn_Close)dlsym(lib, "NeAACDecClose");
    if (!pOpen || !pInit2 || !pReset || !pDecode || !pClose) return 3;

    FILE *fin = fopen(in_path, "rb");
    if (!fin) return 4;
    fseek(fin, 0, SEEK_END);
    long fsize = ftell(fin);
    fseek(fin, 0, SEEK_SET);
    if (fsize < 256) { fclose(fin); return 5; }

    unsigned char *buf = (unsigned char*)malloc(fsize);
    if (!buf) { fclose(fin); return 6; }
    if (fread(buf, 1, fsize, fin) != (size_t)fsize) { free(buf); fclose(fin); return 7; }
    fclose(fin);

    int sr_list[] = {4, 3, 5, 6, 8, 7, 11, 9, 10};
    int ch_list[] = {1, 2};

    int best_sr = -1, best_ch = -1;
    long best_off = -1;
    int max_chain = 0;

    long scan_limit = (fsize < 131072) ? fsize - 128 : 131072;

    for (int pass = 0; pass < 2 && best_sr == -1; pass++) {
        for (int s = 0; s < 9 && best_sr == -1; s++) {
            int sr_idx = sr_list[s];
            if (pass == 0 && hint_sr_idx >= 0 && sr_idx != hint_sr_idx) continue;
            for (int c = 0; c < 2 && best_sr == -1; c++) {
                int ch_cfg = ch_list[c];
                if (pass == 0 && hint_ch > 0 && ch_cfg != hint_ch) continue;

                unsigned short asc_val = (2 << 11) | (sr_idx << 7) | (ch_cfg << 3);
                unsigned char asc[2] = { (asc_val >> 8) & 0xFF, asc_val & 0xFF };

                NeAACDecHandle hDec = pOpen();
                unsigned long sr_out = 0;
                unsigned char ch_out = 0;
                if (pInit2(hDec, asc, 2, &sr_out, &ch_out) < 0) {
                    pClose(hDec);
                    continue;
                }

                for (long off = 0; off < scan_limit; off++) {
                    unsigned char first_id = (buf[off] >> 5) & 0x07;
                    if (ch_cfg == 1 && first_id != 0 && first_id != 4 && first_id != 6) continue;
                    if (ch_cfg == 2 && first_id != 1 && first_id != 0 && first_id != 4 && first_id != 6) continue;

                    pReset(hDec, -1);
                    long cur = off;
                    int chain = 0;
                    while (cur + 16 < fsize && chain < 25) {
                        NeAACDecFrameInfo info;
                        memset(&info, 0, sizeof(info));
                        long rem = fsize - cur;
                        if (rem > 4096) rem = 4096;
                        pDecode(hDec, &info, buf + cur, rem);
                        if (info.error == 0 && info.bytesconsumed >= 6 && info.bytesconsumed <= 2048 && info.samples >= 512) {
                            cur += info.bytesconsumed;
                            chain++;
                        } else {
                            break;
                        }
                    }
                    if (chain >= 20) {
                        best_sr = sr_idx;
                        best_ch = ch_cfg;
                        best_off = off;
                        break;
                    }
                }
                pClose(hDec);
            }
        }
    }

    if (best_sr == -1) {
        free(buf);
        return 8;
    }

    FILE *fout = fopen(out_path, "wb");
    if (!fout) { free(buf); return 9; }

    unsigned short asc_val = (2 << 11) | (best_sr << 7) | (best_ch << 3);
    unsigned char asc[2] = { (asc_val >> 8) & 0xFF, asc_val & 0xFF };
    NeAACDecHandle hDec = pOpen();
    unsigned long sr_out = 0;
    unsigned char ch_out = 0;
    pInit2(hDec, asc, 2, &sr_out, &ch_out);

    long pos = best_off;
    long frames_written = 0;
    while (pos + 7 < fsize) {
        NeAACDecFrameInfo info;
        memset(&info, 0, sizeof(info));
        long rem = fsize - pos;
        if (rem > 4096) rem = 4096;
        pDecode(hDec, &info, buf + pos, rem);
        if (info.error == 0 && info.bytesconsumed >= 6 && info.bytesconsumed <= 2048 && info.samples >= 512) {
            write_adts(fout, best_sr, best_ch, (int)info.bytesconsumed);
            fwrite(buf + pos, 1, info.bytesconsumed, fout);
            pos += info.bytesconsumed;
            frames_written++;
        } else {
            pReset(hDec, -1);
            pos++;
        }
    }

    pClose(hDec);
    fclose(fout);
    free(buf);
    printf("RESCUED_FRAMES=%ld SR_IDX=%d CH=%d\n", frames_written, best_sr, best_ch);
    return (frames_written > 20) ? 0 : 10;
}
"""

def init_repair_environment():
    """نصب و کامپایل خودکار ماژول بازیابی فریم‌های خام M4A در صورت وجود دسترسی سیستم"""
    try:
        # بررسی وجود libfaad.so.2
        has_faad = False
        for libname in ["libfaad.so.2", "libfaad.so"]:
            try:
                ctypes.CDLL(libname)
                has_faad = True
                break
            except Exception:
                pass

        if not has_faad and shutil.which("apt-get"):
            print("[Init] Installing libfaad2 & gpac for deep M4A forensic recovery...", flush=True)
            prefix = ["sudo", "-n"] if os.geteuid() != 0 and shutil.which("sudo") else []
            subprocess.run(prefix + ["apt-get", "update", "-y"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
            subprocess.run(prefix + ["apt-get", "install", "-y", "libfaad2", "faad", "gpac", "gcc"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)

        if not os.path.exists(FAAD_BIN_PATH) and shutil.which("gcc"):
            c_file = "faad_mdat_rescue.c"
            with open(c_file, "w", encoding="utf-8") as f:
                f.write(C_RESCUE_SOURCE)
            res = subprocess.run(["gcc", "-O2", c_file, "-ldl", "-o", FAAD_BIN_PATH], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if os.path.exists(c_file):
                os.remove(c_file)
            if res.returncode == 0:
                print("[Init] Compiled C M4A/AAC forensic scanner successfully.", flush=True)
    except Exception as e:
        print(f"[Init Environment Note]: {e}", flush=True)

# ==================== روبیکا ====================
def send_rubika_msg(chat_id, text):
    url = f"{RUBIKA_API}/sendMessage"
    payload = {"chat_id": str(chat_id), "text": text}
    try:
        requests.post(url, json=payload, headers=HEADERS, timeout=10)
    except Exception as e:
        print(f"[Rubika Send Error]: {e}", flush=True)

def get_rubika_file_info(file_id):
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
        size = int(data_obj.get("size") or res.get("size") or 0)
        return (str(dl_url).strip() if dl_url else None), size
    except Exception as e:
        print(f"[Rubika File Error]: {e}", flush=True)
        return None, 0

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

# ==================== دانلودر پیشرفته بلوک‌به‌بلوک ====================
def is_valid_binary_chunk(data):
    if not data:
        return False
    stripped = data.lstrip()
    if len(stripped) < 300 and (
        stripped.startswith(b'{"status"')
        or stripped.startswith(b"<!DOCTYPE")
        or stripped.startswith(b"<html")
    ):
        return False
    return True

def is_valid_downloaded_file(path):
    if not os.path.exists(path) or os.path.getsize(path) <= 64:
        return False
    try:
        with open(path, "rb") as f:
            head = f.read(512)
        return is_valid_binary_chunk(head)
    except Exception:
        return False

def download_chunked_rubika(url, dest_path, expected_size=0, timeout=180):
    header_profiles = [
        lambda s, e: {**HEADERS, "start-byte": str(s), "last-byte": str(e), "Range": f"bytes={s}-{e}"},
        lambda s, e: {**HEADERS, "start-byte": str(s), "last-byte": str(e)},
        lambda s, e: {**HEADERS, "Range": f"bytes={s}-{e}"},
    ]

    for profile_idx, make_headers in enumerate(header_profiles):
        for method in ["GET", "POST"]:
            offset = 0
            chunk_size = 262144
            total_written = 0
            consecutive_failures = 0
            session = requests.Session()

            try:
                with open(dest_path, "wb") as f:
                    while True:
                        end_byte = offset + chunk_size - 1
                        req_headers = make_headers(offset, end_byte)
                        try:
                            if method == "GET":
                                r = session.get(url, headers=req_headers, timeout=25, verify=False, allow_redirects=True)
                            else:
                                r = session.post(url, headers=req_headers, timeout=25, verify=False, allow_redirects=True)

                            content = r.content
                            if r.status_code in (200, 206) and is_valid_binary_chunk(content):
                                f.write(content)
                                f.flush()
                                received_len = len(content)
                                total_written += received_len
                                offset += received_len
                                consecutive_failures = 0

                                total_len_hdr = int(r.headers.get("total_length") or 0)
                                if total_len_hdr > 0 and offset >= total_len_hdr:
                                    break
                                if expected_size > 0 and offset >= expected_size:
                                    break
                                if received_len < chunk_size and (r.status_code == 200 and "Content-Range" not in r.headers and not r.headers.get("total_length")):
                                    break
                                if received_len == 0:
                                    break
                            else:
                                if total_written > 0 and chunk_size > 16384:
                                    chunk_size = 16384
                                    continue
                                consecutive_failures += 1
                                if consecutive_failures >= 2:
                                    break
                        except Exception as e:
                            print(f"[Chunk Error at offset {offset}]: {e}", flush=True)
                            if total_written > 0 and chunk_size > 16384:
                                chunk_size = 16384
                                continue
                            break

                if total_written > 64 and is_valid_downloaded_file(dest_path):
                    print(f"[Chunked Download Success] Profile {profile_idx+1} ({method}): {total_written} bytes", flush=True)
                    return True
            except Exception as e:
                print(f"[Chunked Session Error]: {e}", flush=True)

    return False

def download_raw_socket_stream(url, dest_path, timeout=120):
    try:
        session = requests.Session()
        r = session.get(url, headers=HEADERS, stream=True, timeout=timeout, verify=False)
        if hasattr(r.raw, "_fp") and r.raw._fp is not None:
            if hasattr(r.raw._fp, "length"):
                r.raw._fp.length = None
        total = 0
        with open(dest_path, "wb") as f:
            while True:
                chunk = r.raw.read(32768)
                if not chunk:
                    break
                f.write(chunk)
                total += len(chunk)
        r.close()
        if total > 64 and is_valid_downloaded_file(dest_path):
            print(f"[Raw Socket Stream Success]: {total} bytes", flush=True)
            return True
    except Exception as e:
        print(f"[Raw Socket Stream Error]: {e}", flush=True)
        if is_valid_downloaded_file(dest_path):
            return True
    return False

def download_file_ultimate(url, dest_path, file_id=None, expected_size=0, timeout=180):
    urls_to_try = [url]
    if url.startswith("https://"):
        urls_to_try.append("http://" + url[len("https://"):])

    for attempt_url in urls_to_try:
        if shutil.which("curl"):
            try:
                cmd_curl = [
                    "curl", "--http1.1", "--ignore-content-length", "-L", "-k", "-s",
                    "--retry", "2", "--max-time", str(timeout),
                    "-A", HEADERS["User-Agent"],
                    "-o", dest_path,
                    attempt_url
                ]
                subprocess.run(cmd_curl, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout + 10)
                if is_valid_downloaded_file(dest_path):
                    print(f"[Download Success via CURL ignore-length]: {os.path.getsize(dest_path)} bytes", flush=True)
                    return True, "OK (curl)"
            except Exception as e:
                print(f"[Curl Error]: {e}", flush=True)

        if download_raw_socket_stream(attempt_url, dest_path, timeout=60):
            return True, "OK (raw-socket)"

        fresh_url, fresh_size = get_rubika_file_info(file_id) if file_id else (attempt_url, expected_size)
        target_url = fresh_url or attempt_url
        if download_chunked_rubika(target_url, dest_path, expected_size=fresh_size or expected_size, timeout=timeout):
            return True, "OK (chunked)"

    return False, f"اندازه ثبت‌شده در سرور روبیکا: {expected_size} بایت"

# ==================== اعتبارسنجی دقیق خروجی (جلوگیری از باگ 0.00MB) ====================
def get_audio_duration(path):
    """دریافت مدت زمان واقعی فایل صوتی به ثانیه با ffprobe"""
    if not os.path.exists(path):
        return 0.0
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15)
        val = res.stdout.strip()
        return float(val) if val and val != "N/A" else 0.0
    except Exception:
        return 0.0

def is_valid_output(output_path, input_size=0):
    """
    بررسی دقیق سلامت و حجم فایل MP3 خروجی متناسب با حجم فایل ورودی.
    هرگز اجازه نمی‌دهد فایل‌های کاذب ۱ کیلوبایتی (0.00MB) به عنوان خروجی سالم تایید شوند.
    """
    if not os.path.exists(output_path):
        return False

    out_size = os.path.getsize(output_path)
    if input_size > 1_000_000:  # برای فایل‌های بالای ۱ مگابایت (مثل جلسات درس)
        min_required = max(35_000, min(int(input_size * 0.015), 200_000))
    elif input_size > 50_000:
        min_required = max(8_000, int(input_size * 0.04))
    else:
        min_required = 2048

    if out_size < min_required:
        return False

    dur = get_audio_duration(output_path)
    if input_size > 500_000 and dur < 5.0:
        return False

    return True

# ==================== موتور جراحی و بازسازی فایل‌های M4A و صوتی خراب ====================
def run_ffmpeg(args, input_bytes=None, timeout=240):
    try:
        res = subprocess.run(
            args,
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout
        )
        return res.returncode == 0, res.stderr.decode("utf-8", errors="ignore")
    except Exception as e:
        return False, str(e)

def inspect_and_patch_mp4_boxes(raw_path, patched_path):
    """
    اصلاح بایت‌های زباله قبل از ftyp و ترمیم اندازهٔ بلوک mdat (۳۲ بیتی و ۶۴ بیتی)
    در فایل‌های M4A/MP4 که ضبط آن‌ها ناگهان قطع شده است.
    """
    try:
        with open(raw_path, "rb") as f:
            data = bytearray(f.read())

        if len(data) < 64:
            return False, -1, -1

        # حذف زباله‌های احتمالی قبل از باکس ftyp
        ftyp_pos = data.find(b"ftyp", 0, min(len(data), 65536))
        if ftyp_pos > 4:
            data = data[ftyp_pos - 4:]

        # استخراج راهنمای نرخ نمونه‌برداری (Sample Rate) در صورت وجود بخشی از جدول stsd/esds
        hint_sr_idx = -1
        hint_ch = -1
        sr_map = {96000: 0, 88200: 1, 64000: 2, 48000: 3, 44100: 4, 32000: 5, 24000: 6, 22050: 7, 16000: 8, 12000: 9, 11025: 10, 8000: 11}
        mp4a_pos = data.find(b"mp4a")
        if mp4a_pos != -1 and mp4a_pos + 28 <= len(data):
            ch = struct.unpack(">H", data[mp4a_pos + 16:mp4a_pos + 18])[0]
            sr = struct.unpack(">H", data[mp4a_pos + 22:mp4a_pos + 24])[0]
            if ch in (1, 2):
                hint_ch = ch
            if sr in sr_map:
                hint_sr_idx = sr_map[sr]

        # بررسی و اصلاح سایز باکس mdat اگر moov در فایل وجود داشته باشد
        moov_pos = data.find(b"moov")
        mdat_pos = data.find(b"mdat")
        modified = (ftyp_pos > 4)

        if mdat_pos >= 4:
            box_start = mdat_pos - 4
            box_size = struct.unpack(">I", data[box_start:mdat_pos])[0]
            # اگر باکس ۶۴ بیتی (Extended Size == 1) باشد و سایز آن ۰ مانده باشد
            if box_size == 1 and mdat_pos + 12 <= len(data):
                ext_size = struct.unpack(">Q", data[mdat_pos + 4:mdat_pos + 12])[0]
                actual_rem = len(data) - box_start
                if ext_size == 0 or ext_size > actual_rem:
                    struct.pack_into(">Q", data, mdat_pos + 4, actual_rem)
                    modified = True
            elif box_size == 0 or (moov_pos != -1 and moov_pos < mdat_pos and box_start + box_size > len(data)):
                actual_rem = len(data) - box_start
                if actual_rem < 0xFFFFFFFF:
                    struct.pack_into(">I", data, box_start, actual_rem)
                    modified = True

        if modified:
            with open(patched_path, "wb") as f:
                f.write(data)

        return modified, hint_sr_idx, hint_ch
    except Exception as e:
        print(f"[MP4 Patch Error]: {e}", flush=True)
        return False, -1, -1

def extract_mdat_payload(raw_path, mdat_out_path):
    """جداسازی دقیق محتوای صوتی خام داخل بلوک mdat (پشتیبانی از هدرهای ۳۲ بیتی و ۶۴ بیتی)"""
    try:
        with open(raw_path, "rb") as f:
            data = f.read()

        mdat_pos = data.find(b"mdat")
        if mdat_pos >= 4:
            box_size = struct.unpack(">I", data[mdat_pos - 4:mdat_pos])[0]
            payload_start = (mdat_pos + 12) if box_size == 1 else (mdat_pos + 4)
            if payload_start < len(data):
                with open(mdat_out_path, "wb") as f:
                    f.write(data[payload_start:])
                return True

        # اگر تگ mdat پیدا نشد ولی فایل هدر شکسته دارد
        stripped = data.lstrip(b"\x00")
        if len(stripped) > 256:
            with open(mdat_out_path, "wb") as f:
                f.write(stripped)
            return True
        return False
    except Exception as e:
        print(f"[Extract mdat Error]: {e}", flush=True)
        return False

def rescue_raw_aac_with_ctypes(mdat_path, adts_out_path, hint_sr_idx=-1, hint_ch=-1):
    """پشتیبان پایتونی (ctypes) برای بازیابی فریم‌های خام AAC با libfaad2 در صورت نبودن gcc"""
    lib = None
    for name in ["libfaad.so.2", "libfaad.so"]:
        try:
            lib = ctypes.CDLL(name)
            break
        except Exception:
            pass
    if not lib:
        return False

    class NeAACDecFrameInfo(ctypes.Structure):
        _fields_ = [
            ("bytesconsumed", ctypes.c_ulong),
            ("samples", ctypes.c_ulong),
            ("channels", ctypes.c_ubyte),
            ("error", ctypes.c_ubyte),
            ("samplerate", ctypes.c_ulong),
            ("sbr", ctypes.c_ubyte),
            ("object_type", ctypes.c_ubyte),
            ("header_type", ctypes.c_ubyte),
            ("num_front_channels", ctypes.c_ubyte),
            ("num_side_channels", ctypes.c_ubyte),
            ("num_back_channels", ctypes.c_ubyte),
            ("num_lfe_channels", ctypes.c_ubyte),
            ("channel_position", ctypes.c_ubyte * 64),
            ("ps", ctypes.c_ubyte),
            ("pad", ctypes.c_ubyte * 128),
        ]

    lib.NeAACDecOpen.restype = ctypes.c_void_p
    lib.NeAACDecInit2.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ubyte)]
    lib.NeAACDecInit2.restype = ctypes.c_char
    lib.NeAACDecPostSeekReset.argtypes = [ctypes.c_void_p, ctypes.c_long]
    lib.NeAACDecDecode.argtypes = [ctypes.c_void_p, ctypes.POINTER(NeAACDecFrameInfo), ctypes.c_char_p, ctypes.c_ulong]
    lib.NeAACDecDecode.restype = ctypes.c_void_p
    lib.NeAACDecClose.argtypes = [ctypes.c_void_p]

    with open(mdat_path, "rb") as f:
        buf = f.read()
    fsize = len(buf)
    if fsize < 256:
        return False

    c_buf = ctypes.create_string_buffer(buf)
    base_addr = ctypes.addressof(c_buf)

    sr_list = [4, 3, 5, 6, 8, 7, 11]
    if hint_sr_idx in sr_list:
        sr_list.remove(hint_sr_idx)
        sr_list.insert(0, hint_sr_idx)
    ch_list = [1, 2] if hint_ch != 2 else [2, 1]

    best_sr, best_ch, best_off = -1, -1, -1
    scan_limit = min(fsize - 128, 65536)

    for sr_idx in sr_list:
        if best_sr != -1:
            break
        for ch_cfg in ch_list:
            asc_val = (2 << 11) | (sr_idx << 7) | (ch_cfg << 3)
            asc = bytes([(asc_val >> 8) & 0xFF, asc_val & 0xFF])
            hDec = lib.NeAACDecOpen()
            sr_out = ctypes.c_ulong(0)
            ch_out = ctypes.c_ubyte(0)
            if ord(lib.NeAACDecInit2(hDec, asc, 2, ctypes.byref(sr_out), ctypes.byref(ch_out))) >= 128:
                lib.NeAACDecClose(hDec)
                continue

            info = NeAACDecFrameInfo()
            for off in range(scan_limit):
                first_id = (buf[off] >> 5) & 0x07
                if ch_cfg == 1 and first_id not in (0, 4, 6):
                    continue
                if ch_cfg == 2 and first_id not in (1, 0, 4, 6):
                    continue

                lib.NeAACDecPostSeekReset(hDec, -1)
                cur = off
                chain = 0
                while cur + 16 < fsize and chain < 20:
                    rem = min(4096, fsize - cur)
                    ptr = ctypes.c_char_p(base_addr + cur)
                    lib.NeAACDecDecode(hDec, ctypes.byref(info), ptr, rem)
                    if info.error == 0 and 6 <= info.bytesconsumed <= 2048 and info.samples >= 512:
                        cur += info.bytesconsumed
                        chain += 1
                    else:
                        break
                if chain >= 18:
                    best_sr, best_ch, best_off = sr_idx, ch_cfg, off
                    break
            lib.NeAACDecClose(hDec)
            if best_sr != -1:
                break

    if best_sr == -1:
        return False

    asc_val = (2 << 11) | (best_sr << 7) | (best_ch << 3)
    asc = bytes([(asc_val >> 8) & 0xFF, asc_val & 0xFF])
    hDec = lib.NeAACDecOpen()
    sr_out = ctypes.c_ulong(0)
    ch_out = ctypes.c_ubyte(0)
    lib.NeAACDecInit2(hDec, asc, 2, ctypes.byref(sr_out), ctypes.byref(ch_out))
    info = NeAACDecFrameInfo()

    pos = best_off
    frames_written = 0
    with open(adts_out_path, "wb") as fout:
        while pos + 7 < fsize:
            rem = min(4096, fsize - pos)
            ptr = ctypes.c_char_p(base_addr + pos)
            lib.NeAACDecDecode(hDec, ctypes.byref(info), ptr, rem)
            if info.error == 0 and 6 <= info.bytesconsumed <= 2048 and info.samples >= 512:
                flen = int(info.bytesconsumed)
                full_len = flen + 7
                hdr = bytes([
                    0xFF, 0xF1,
                    (1 << 6) | ((best_sr & 0x0F) << 2) | ((best_ch >> 2) & 0x01),
                    ((best_ch & 0x03) << 6) | ((full_len >> 11) & 0x03),
                    (full_len >> 3) & 0xFF,
                    ((full_len & 0x07) << 5) | 0x1F,
                    0xFC
                ])
                fout.write(hdr)
                fout.write(buf[pos:pos + flen])
                pos += flen
                frames_written += 1
            else:
                lib.NeAACDecPostSeekReset(hDec, -1)
                pos += 1

    lib.NeAACDecClose(hDec)
    return frames_written > 20

def recover_unfinalized_m4a_mdat(raw_path, adts_out_path, hint_sr_idx=-1, hint_ch=-1):
    """
    بازسازی کامل فایل‌های M4A فاقد جدول moov از طریق استخراج بلوک mdat
    و رمزگشایی مرز فریم‌های خام AAC با موتور C/libfaad2
    """
    mdat_path = raw_path + ".mdat"
    try:
        if not extract_mdat_payload(raw_path, mdat_path):
            return False

        # ۱. استفاده از باینری سریع C در صورت وجود
        if os.path.exists(FAAD_BIN_PATH):
            cmd = [FAAD_BIN_PATH, mdat_path, adts_out_path, str(hint_sr_idx), str(hint_ch)]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=180)
            print(f"[FAAD C Rescue Output]: {res.stdout.strip()} (code={res.returncode})", flush=True)
            if res.returncode == 0 and os.path.exists(adts_out_path) and os.path.getsize(adts_out_path) > 4096:
                return True

        # ۲. پشتیبان پایتونی ctypes
        if rescue_raw_aac_with_ctypes(mdat_path, adts_out_path, hint_sr_idx, hint_ch):
            print("[FAAD ctypes Rescue Success]", flush=True)
            return True

        return False
    except Exception as e:
        print(f"[Unfinalized M4A Rescue Error]: {e}", flush=True)
        return False
    finally:
        if os.path.exists(mdat_path):
            try:
                os.remove(mdat_path)
            except Exception:
                pass

def carve_clean_stream(raw_path, carved_path):
    """جستجوی هدرهای استاندارد صوتی (OggS, ID3, #!AMR, ADTS Sync) در فایل‌های آسیب‌دیده"""
    try:
        with open(raw_path, "rb") as f:
            data = f.read()

        if len(data) < 64:
            return False

        for sig in [b"OggS", b"ID3", b"#!AMR", b"fLaC", b"RIFF"]:
            pos = data.find(sig)
            if pos > 0:
                with open(carved_path, "wb") as f:
                    f.write(data[pos:])
                return True

        # بررسی وجود فریم‌های واقعی ADTS (بررسی اینکه طول فریم به سینک بعدی برسد)
        limit = min(len(data) - 10, 131072)
        for i in range(limit):
            if data[i] == 0xFF and (data[i + 1] & 0xF6) == 0xF0:
                frame_len = ((data[i + 3] & 0x03) << 11) | (data[i + 4] << 3) | ((data[i + 5] >> 5) & 0x07)
                if 7 < frame_len < 2048 and i + frame_len + 2 < len(data):
                    if data[i + frame_len] == 0xFF and (data[i + frame_len + 1] & 0xF6) == 0xF0:
                        with open(carved_path, "wb") as f:
                            f.write(data[i:])
                        return True
        return False
    except Exception as e:
        print(f"[Carve Error]: {e}", flush=True)
        return False

def repair_and_convert_to_mp3(input_source, output_path, target_kbps="24k", is_url=False):
    """
    موتور چندلایهٔ جراحی، ترمیم و تبدیل فایل‌های صوتی (به‌ویژه M4A های ناقص یا بدون جدول moov)
    """
    input_size = os.path.getsize(input_source) if (not is_url and os.path.exists(input_source)) else 0
    base_encode = [
        "-vn", "- max_muxing_queue_size".replace(" ", ""), "9999",
        "-c:a", "libmp3lame", "-ac", "1", "-ar", "24000", "-b:a", target_kbps,
        output_path
    ]

    # --- مرحله ۱: رمزگشایی مستقیم با نادیده گرفتن Edit List های خراب (-ignore_editlist 1) ---
    cmd1 = [
        "ffmpeg", "-y",
        "-ignore_editlist", "1",
        "-fflags", "+discardcorrupt+genpts",
        "-err_detect", "ignore_err",
        "-i", input_source
    ] + base_encode
    ok, err = run_ffmpeg(cmd1)
    if is_valid_output(output_path, input_size):
        print("[Repair] Stage 1 Success (Direct with -ignore_editlist 1)", flush=True)
        return True
    print(f"[Stage 1 Log]: {err[-300:]}", flush=True)

    if is_url:
        return False

    # --- مرحله ۲: اصلاح باکس‌های هدر MP4/M4A (سایز mdat و حذف زباله‌های ابتدایی) یا بازسازی با MP4Box ---
    patched_path = input_source + ".patched.m4a"
    hint_sr_idx, hint_ch = -1, -1
    try:
        modified, hint_sr_idx, hint_ch = inspect_and_patch_mp4_boxes(input_source, patched_path)
        if modified and os.path.exists(patched_path):
            cmd2 = [
                "ffmpeg", "-y",
                "-ignore_editlist", "1",
                "-fflags", "+discardcorrupt+genpts",
                "-err_detect", "ignore_err",
                "-i", patched_path
            ] + base_encode
            run_ffmpeg(cmd2)
            if is_valid_output(output_path, input_size):
                print("[Repair] Stage 2A Success (Patched MP4 Box Headers)", flush=True)
                return True

        if shutil.which("MP4Box"):
            mp4box_fixed = input_source + ".mp4box.m4a"
            subprocess.run(["MP4Box", "-inter", "500", input_source, "-out", mp4box_fixed], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
            if os.path.exists(mp4box_fixed):
                run_ffmpeg(["ffmpeg", "-y", "-ignore_editlist", "1", "-err_detect", "ignore_err", "-i", mp4box_fixed] + base_encode)
                try:
                    os.remove(mp4box_fixed)
                except Exception:
                    pass
                if is_valid_output(output_path, input_size):
                    print("[Repair] Stage 2B Success (MP4Box Remux)", flush=True)
                    return True
    finally:
        if os.path.exists(patched_path):
            try:
                os.remove(patched_path)
            except Exception:
                pass

    # --- مرحله ۳: بازیابی عمیق فایل‌های M4A نهایی‌نشده (فاقد moov atom) از طریق اسکن فریم‌های خام mdat ---
    adts_rescued_path = input_source + ".rescued.aac"
    try:
        if recover_unfinalized_m4a_mdat(input_source, adts_rescued_path, hint_sr_idx, hint_ch):
            cmd3 = [
                "ffmpeg", "-y",
                "-fflags", "+discardcorrupt+genpts",
                "-err_detect", "ignore_err",
                "-f", "aac",
                "-i", adts_rescued_path
            ] + base_encode
            run_ffmpeg(cmd3)
            if is_valid_output(output_path, input_size):
                print("[Repair] Stage 3 Success (Forensic Raw mdat AAC Frame Reconstruction!)", flush=True)
                return True
    finally:
        if os.path.exists(adts_rescued_path):
            try:
                os.remove(adts_rescued_path)
            except Exception:
                pass

    # --- مرحله ۴: برش هدرهای سوخته (OggS / ID3 / AMR / ADTS) و اجبار دیموکسر به ترتیب امن ---
    carved_path = input_source + ".carved"
    try:
        sources_to_try = [input_source]
        if carve_clean_stream(input_source, carved_path):
            sources_to_try.insert(0, carved_path)

        # نکته مهم: فرمت mp3 در انتهای لیست قرار دارد و با is_valid_output کنترل می‌شود تا خروجی 0.00MB کاذب نسازد
        for src in sources_to_try:
            for fmt in ["aac", "ogg", "amr", "flac", "wav", "mp3"]:
                if os.path.exists(output_path):
                    try:
                        os.remove(output_path)
                    except Exception:
                        pass
                cmd4 = [
                    "ffmpeg", "-y",
                    "-fflags", "+discardcorrupt+genpts+igndts",
                    "-err_detect", "ignore_err",
                    "-f", fmt,
                    "-i", src
                ] + base_encode
                run_ffmpeg(cmd4)
                if is_valid_output(output_path, input_size):
                    print(f"[Repair] Stage 4 Success (Forced Format: {fmt})", flush=True)
                    return True
    finally:
        if os.path.exists(carved_path):
            try:
                os.remove(carved_path)
            except Exception:
                pass

    return False

# ==================== پردازش فایل ====================
def process_single_task(task):
    file_id = task["file_id"]
    original_name = task["original_name"]
    msg_file_size = task.get("file_size", 0)
    rubika_chat_id = task["rubika_chat_id"]
    bale_chat_id = task["bale_chat_id"]

    send_rubika_msg(rubika_chat_id, "⏳ دریافت لینک و استخراج بلوک‌های فایل از روبیکا آغاز شد...")
    download_url, api_file_size = get_rubika_file_info(file_id)
    expected_size = api_file_size or msg_file_size

    if not download_url:
        send_rubika_msg(rubika_chat_id, "❌ دریافت آدرس دانلود فایل از سرور روبیکا ناموفق بود.")
        return

    ext = os.path.splitext(original_name)[1] or ".m4a"
    raw_path = f"raw_{file_id}{ext}"
    mp3_path = f"out_{file_id}.mp3"
    light_path = f"light_{file_id}.mp3"

    try:
        success_download, msg = download_file_ultimate(
            download_url,
            raw_path,
            file_id=file_id,
            expected_size=expected_size,
            timeout=180
        )

        converted = False
        if success_download:
            raw_size_kb = os.path.getsize(raw_path) / 1024
            send_rubika_msg(rubika_chat_id, f"🛠 فایل استخراج شد ({raw_size_kb:.1f} KB). در حال تعمیر و تبدیل به MP3...")
            converted = repair_and_convert_to_mp3(raw_path, mp3_path, target_kbps="24k", is_url=False)
        else:
            send_rubika_msg(rubika_chat_id, "⚡ در حال تلاش نهایی برای استریم مستقیم از سرور روبیکا...")
            fresh_url, _ = get_rubika_file_info(file_id)
            converted = repair_and_convert_to_mp3(fresh_url or download_url, mp3_path, target_kbps="24k", is_url=True)

        if not converted or not os.path.exists(mp3_path):
            send_rubika_msg(
                rubika_chat_id,
                "❌ ساختار باینری این فایل فاقد فریم‌های صوتی استاندارد قابل بازیابی بود."
            )
            return

        size_mb = os.path.getsize(mp3_path) / (1024 * 1024)
        dur_min = int(get_audio_duration(mp3_path) // 60)
        dur_sec = int(get_audio_duration(mp3_path) % 60)
        print(f"[Repaired MP3]: {size_mb:.2f} MB, Duration: {dur_min}:{dur_sec:02d}", flush=True)

        clean_title = os.path.splitext(original_name)[0] if original_name else "صوت"
        send_rubika_msg(
            rubika_chat_id,
            f"🚀 صوت تعمیر شد (حجم: {size_mb:.2f}MB | زمان: {dur_min}:{dur_sec:02d}). در حال ارسال به بله..."
        )

        if send_bale_audio(bale_chat_id, mp3_path, title=clean_title):
            send_rubika_msg(rubika_chat_id, "✅ فایل با موفقیت تعمیر و به بله تحویل داده شد!")
        else:
            print("[Retrying with 16k ultra-light]...", flush=True)
            if repair_and_convert_to_mp3(mp3_path, light_path, target_kbps="16k") and send_bale_audio(bale_chat_id, light_path, title=clean_title):
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
    init_repair_environment()
    print(">>> ربات با موتور بازسازی فریم‌های خام M4A و اعتبارسنجی دقیق صوت فعال شد...", flush=True)

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
                    file_size = int(file_info.get("size") or 0)

                    task_queue.put({
                        "file_id": file_id,
                        "original_name": file_name,
                        "file_size": file_size,
                        "rubika_chat_id": chat_id,
                        "bale_chat_id": user["bale_id"]
                    })
                    send_rubika_msg(chat_id, f"📥 فایل دریافت شد و در صف قرار گرفت (نوبت: {task_queue.qsize()})")

        except Exception as e:
            print(f"[Polling Error]: {e}", flush=True)

        time.sleep(2)

# --- دیسپچ اجرای بعدی ورک‌فلو: زنجیره خودکار اجراها ---
# موقع خاموشی (SIGINT با exec python)، اجرای بعدی همین ورک‌فلو را با
# GITHUB_TOKEN دیسپچ می‌کند تا اگر cron گیت‌هاب جا ماند، زنجیره قطع نشود.
# (رویداد workflow_dispatch با GITHUB_TOKEN هم اجرای جدید می‌سازد.)
# اول چک می‌کند اجرای دیگری در صف یا در حال اجرا نباشد تا اجرای تکراری
# ساخته نشود — مثلاً وقتی لغو به‌خاطر cron بوده و اجرای بعدی از قبل هست.
def _dispatch_next_run():
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    run_id = os.environ.get("GITHUB_RUN_ID")
    if not token or not repo or not run_id:
        print("⚠️ دیسپچ اجرای بعدی انجام نشد: GITHUB_TOKEN یا اطلاعات اجرا در دسترس نیست.", flush=True)
        return
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        r = requests.get(
            f"https://api.github.com/repos/{repo}/actions/workflows/bot.yml/runs?per_page=10",
            headers=headers, timeout=15)
        if r.status_code == 200:
            for run in r.json().get("workflow_runs", []):
                if str(run.get("id")) != str(run_id) and run.get("status") in ("queued", "in_progress", "waiting", "requested", "pending"):
                    print(f"⏭️ اجرای بعدی از قبل وجود دارد (run #{run.get('id')})؛ دیسپچ انجام نشد.", flush=True)
                    return
        else:
            print(f"⚠️ بررسی اجراهای موجود ناموفق بود (HTTP {r.status_code})؛ دیسپچ انجام می‌شود.", flush=True)
    except Exception as e:
        print(f"⚠️ بررسی اجراهای موجود ناموفق بود ({e})؛ دیسپچ انجام می‌شود.", flush=True)
    try:
        ref = os.environ.get("GITHUB_REF_NAME", "main")
        r = requests.post(
            f"https://api.github.com/repos/{repo}/actions/workflows/bot.yml/dispatches",
            headers=headers, json={"ref": ref}, timeout=15)
        if r.status_code == 204:
            print("✅ اجرای بعدی ورک‌فلو با موفقیت دیسپچ شد.", flush=True)
        else:
            print(f"⚠️ دیسپچ اجرای بعدی ناموفق بود (HTTP {r.status_code}): {r.text[:200]}", flush=True)
    except Exception as e:
        print(f"⚠️ دیسپچ اجرای بعدی ناموفق بود: {e}", flush=True)

if __name__ == "__main__":
    try:
        run_bot()
    except KeyboardInterrupt:
        print("🛑 سیگنال توقف دریافت شد. در حال دیسپچ اجرای بعدی...", flush=True)
        _dispatch_next_run()
