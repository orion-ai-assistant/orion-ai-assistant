import sounddevice as sd
import numpy as np
from faster_whisper import WhisperModel
import time
import sys
import queue
import re
import msvcrt
import wave
import threading

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from ctypes import cast, POINTER
from comtypes import CLSCTX_ALL
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume


# =========================================================
# AYARLAR
# =========================================================

import os
# Servis klasörü içindeki models yolunu otomatik bul
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_MODEL = os.path.join(CURRENT_DIR, "models", "whisper-small-finetuned-tr")
MODEL_SIZE = LOCAL_MODEL if os.path.exists(LOCAL_MODEL) else r"C:\Whisper-Finetune-App\models\whisper-small-finetuned-ct2-chkpt-1200"

# Cihaz Seçimi: "cuda" (GPU) veya "cpu" (İşlemci)
DEVICE = "cuda"  # CPU'da denemek için "cpu", GPU için "cuda" yapabilirsiniz

# CPU için en hızlı ve uyumlu mod "int8" veya "float32"'dir.
# GPU için "int8_float16" veya "float16"'dır.
if DEVICE == "cpu":
    COMPUTE_TYPE = "int8"
    # CPU'da maksimum hız için sistemdeki tüm mantıksal/fiziksel çekirdekleri kullanalım
    CPU_THREADS = os.cpu_count() or 4
else:
    COMPUTE_TYPE = "int8_float16"
    CPU_THREADS = 0

LIVE_BEAM_SIZE = 2
FINAL_BEAM_SIZE = 4

RATE = 16000

# 1600 = 100 ms (hızlı ve akıcı ses toplama)
CHUNK = 1600

SILENCE_THRESHOLD = 0.01

# İlk transcribe için gereken minimum ses
MIN_AUDIO_TO_PROCESS = 0.6

# Canlı transcribe aralığı
MIN_TRANSCRIBE_INTERVAL = 0.2

# Cümle bitişi (LLM için hızlı ama yavaş konuşanları kesmeyecek güvenli sınır: 0.4s)
# Dil seçimi: Çevre değişkeni veya None (auto detect için)
_env_lang = os.getenv("WHISPER_LANGUAGE", "").strip()
STREAM_LANGUAGE = None if (not _env_lang or _env_lang.lower() in ("auto", "none")) else _env_lang

# =========================================================

audio_queue = queue.Queue()


def audio_callback(indata, frames, time_info, status):
    if status:
        pass
    audio_queue.put(indata.copy())


def main():

    # =========================================================
    # WINDOWS MİKROFON DONANIM KONTROLÜ
    # =========================================================
    try:
        devices = AudioUtilities.GetMicrophone()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = cast(interface, POINTER(IAudioEndpointVolume))
        
        # Windows'taki güncel mikrofon seviyesini (0.0 ile 1.0 arası) oku
        current_vol = volume.GetMasterVolumeLevelScalar()
        
        # Eğer Windows ses ayarlarına girip mikrofonu %80'in altına düşürmüşse:
        if current_vol < 0.80:
            print(f"⚠️ DİKKAT: Windows Mikrofon sesiniz çok düşük! (%{current_vol*100:.0f})")
            print("🚀 Kod, en iyi yapay zeka performansı için Windows mikrofon sesinizi otomatik olarak %100'e yükseltiyor...")
            # Windows'taki mikrofon ses çubuğunu zorla %100 (1.0) yap!
            volume.SetMasterVolumeLevelScalar(1.0, None)
        else:
            print(f"✅ Windows Mikrofon Donanım Seviyesi İdeal: %{current_vol*100:.0f}")
            
    except Exception as e:
        print(f"Windows mikrofonuna erişilirken bir hata oluştu (Önemli değil, devam ediliyor): {e}")
    # =========================================================

    print(
        f"[{MODEL_SIZE}] model yükleniyor "
        f"({DEVICE.upper()}, {COMPUTE_TYPE})..."
    )

    model = WhisperModel(
        MODEL_SIZE,
        device=DEVICE,
        compute_type=COMPUTE_TYPE,
        cpu_threads=CPU_THREADS
    )

    print("Model yüklendi!\n")

    print(
        "\n======================================================="
    )

    print(
        "GERÇEK ZAMANLI DİNLEME BAŞLADI..."
    )

    print(
        "Durdurmak için Ctrl+C | Son sesi dinlemek için D tuşu"
    )

    print(
        f"CHUNK: {CHUNK / RATE:.2f}s | "
        f"Min buffer: {MIN_AUDIO_TO_PROCESS}s | "
        f"Live interval: {MIN_TRANSCRIBE_INTERVAL}s"
    )

    print(
        "=======================================================\n"
    )

    # =====================================================
    # STATE
    # =====================================================

    accumulated_audio = np.array(
        [],
        dtype=np.float32
    )

    silence_duration = 0.0

    last_transcribe_time = 0.0

    # Console'da yalnızca tek canlı satır
    last_live_text = ""

    last_speech_time = time.perf_counter()
    speech_start_time = None

    noise_ema = None
    last_infer_duration = 0.0

    last_final_audio = np.array([], dtype=np.float32)
    debug_audio_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "debug_audio")
    os.makedirs(debug_audio_dir, exist_ok=True)

    # -----------------------------------------------------
    # PROMPT
    # -----------------------------------------------------

    # Whisper boşlukta bu kelimeleri uydurmasın diye base_prompt'u küçülttük
    base_prompt = (
        "Hmm... Naber? İyii, senden naberr?"
        "İyi bende saol. GPU ayarlarını güncelledim! "
        "Bir saat sonraya alarm kur."
        "Ama... Spotify'dan blok 3 açar mısın? "
    )
    context_text = ""

    try:
        with sd.InputStream(
            channels=1,
            samplerate=RATE,
            blocksize=CHUNK,
            callback=audio_callback,
        ):
            while True:

                # Klavye kontrolu (D = son sesi dinle)
                if msvcrt.kbhit():
                    key = msvcrt.getch().lower()
                    if key == b'd':
                        if len(last_final_audio) > 0:
                            peak_val = np.max(np.abs(last_final_audio))
                            sys.stdout.write("\r" + (" " * 250) + "\r")
                            sys.stdout.write(
                                f"[{time.strftime('%X')}] "
                                f"(DINLE) Modele giden ses oynatılıyor ({len(last_final_audio)/RATE:.1f}sn, Model Tepe: {peak_val:.2f})...\n"
                            )
                            sys.stdout.flush()
                            
                            # Kullanıcının tam olarak modele giden sesi (Yapay zekanın duyduğu haliyle) duymasını sağlıyoruz
                            play_audio = last_final_audio.copy()
                            
                            sd.play(play_audio, samplerate=RATE)
                            sd.wait()
                            # Oynatma sirasinda biriken sesleri temizle
                            while not audio_queue.empty():
                                audio_queue.get()
                            accumulated_audio = np.array([], dtype=np.float32)
                            silence_duration = 0.0
                            last_live_text = ""
                            speech_start_time = None
                            sys.stdout.write(
                                f"[{time.strftime('%X')}] "
                                f"(DINLE) Oynatma bitti, dinleme devam ediyor...\n"
                            )
                            sys.stdout.flush()
                        else:
                            sys.stdout.write("\r" + (" " * 250) + "\r")
                            sys.stdout.write(
                                f"[{time.strftime('%X')}] "
                                f"(DINLE) Henuz oynatilacak ses yok.\n"
                            )
                            sys.stdout.flush()

                chunks = [audio_queue.get().flatten()]
                while not audio_queue.empty():
                    chunks.append(audio_queue.get().flatten())
                
                chunk_data = np.concatenate(chunks)

                chunk_time = (
                    len(chunk_data)
                    / RATE
                )

                # =================================================
                # CONTEXT SIFIRLAMA (10 Saniye kuralı)
                # =================================================
                
                current_time = time.perf_counter()
                if context_text != "" and (current_time - last_speech_time > 10.0):
                    context_text = ""
                    # Konsolu kirletmemek için çok kısa bir log basıyoruz
                    sys.stdout.write(f"\r[{time.strftime('%X')}] (Hafıza 10sn sessizlik ile temizlendi)          \n")
                    sys.stdout.flush()

                # =================================================
                # RMS / SESSİZLİK (GÜRÜLTÜ ZEMİNİ TAKİBİ)
                # =================================================

                rms = np.sqrt(
                    np.mean(
                        chunk_data ** 2
                    )
                )

                if noise_ema is None:
                    noise_ema = rms

                # Konuşma eşiği: Ortam gürültüsünün 2.5 katı (Nefes seslerini elemeye yardımcı olur)
                # Tamamen dijital sıfır gelmesine karşı da minimum 0.001 zorunluluğu var
                current_threshold = max(0.001, noise_ema * 2.5)

                if rms < current_threshold:
                    
                    # Ses eşiğin altındaysa sessizlik süresini artır
                    silence_duration += chunk_time
                    
                    # Ortam gürültüsünü hızlıca güncelle (çünkü şu an konuşma olmadığından eminiz)
                    noise_ema = noise_ema * 0.9 + rms * 0.1

                else:

                    silence_duration = 0.0
                    
                    if speech_start_time is None:
                        speech_start_time = time.perf_counter() - chunk_time
                    
                    # Konuşma sırasında gürültü zeminini ÇOK YAVAŞ güncelle 
                    # (Böylece arka planda sürekli bir fan açılsa bile yavaş yavaş alışır, ama konuşma sırasında eşiği bozmaz)
                    noise_ema = noise_ema * 0.999 + rms * 0.001

                # =================================================
                # SESİ BUFFER'A EKLE
                # =================================================

                accumulated_audio = np.concatenate(
                    (
                        accumulated_audio,
                        chunk_data
                    )
                )

                audio_duration = (
                    len(accumulated_audio)
                    / RATE
                )

                # =================================================
                # CÜMLE BİTTİ Mİ?
                # =================================================

                sentence_ended = (

                    silence_duration
                    >= SILENCE_DURATION_TO_END

                    and

                    audio_duration
                    >= MIN_AUDIO_TO_PROCESS
                )

                # =================================================
                # TRANSCRIBE ZAMANI
                # =================================================

                now = time.perf_counter()

                elapsed = (
                    now
                    - last_transcribe_time
                )

                # Eğer bilgisayar yavaş kalıyorsa, kuyruğu şişirmemek için aralığı dinamik olarak açıyoruz
                dynamic_interval = max(MIN_TRANSCRIBE_INTERVAL, last_infer_duration * 1.5)

                should_transcribe = (

                    audio_duration
                    >= MIN_AUDIO_TO_PROCESS

                    and

                    (
                        sentence_ended
                        or
                        elapsed
                        >= dynamic_interval
                    )
                )

                if not should_transcribe:
                    continue

                last_transcribe_time = now

                # =================================================
                # BEAM
                # =================================================

                active_beam_size = (

                    FINAL_BEAM_SIZE
                    if sentence_ended
                    else LIVE_BEAM_SIZE
                )

                # =================================================
                # PROMPT
                # =================================================

                current_prompt = (
                    base_prompt
                    + " "
                    + (
                        context_text[-200:]
                        if context_text
                        else ""
                    )
                )

                # =================================================
                # SES VERİSİ
                # =================================================
                
                audio_to_process = accumulated_audio.copy()
                # =================================================
                # PEAK NORMALIZATION (YAPAY ZEKA İÇİN STANDARTLAŞTIRMA)
                # =================================================
                # Her mikrofonun (100%'de bile olsa) hassasiyeti farklıdır.
                # Gürültü temizlendikten SONRA, sırf "saf insan sesini" Whisper'ın 
                # en sevdiği tepe noktasına (0.35) sabitliyoruz.
                max_amp = np.max(np.abs(audio_to_process))
                if max_amp > 0.005:
                    audio_to_process = (audio_to_process * (0.35 / max_amp)).astype(np.float32)

                # =================================================
                # WHISPER
                # =================================================

                start_time = (
                    time.perf_counter()
                )

                segments, info = model.transcribe(

                    audio_to_process,

                    beam_size=active_beam_size,

                    language=STREAM_LANGUAGE,

                    vad_filter=True,

                    vad_parameters=dict(
                        min_silence_duration_ms=500,
                        speech_pad_ms=400,
                        min_speech_duration_ms=150, # Klavye ile insan sesini ayırmak için ideal nokta
                        threshold=0.5               # Hafif tıkırtıları engellemek için
                    ),

                    no_speech_threshold=0.6,
                    
                    without_timestamps=True,

                    condition_on_previous_text=False,

                    initial_prompt=current_prompt,

                    temperature=0.0,

                    compression_ratio_threshold=2.0, # (2.4'tü) Tekrarlayan "Hıhıhıhı..." gibi halüsinasyonları daha sert engeller

                    log_prob_threshold=-1.0,
                )

                text = ""

                for segment in segments:

                    if segment.no_speech_prob < 0.6: # 0.4'tü geri çektim

                        text += segment.text

                text = text.strip()

                # =================================================
                # HALÜSİNASYON ENGELLEYİCİ (Manuel Filtre)
                # =================================================
                
                if text:
                    # 1. Aynı harfin 5 kereden fazla tekrarı (örn: ıııııııı -> ıı)
                    text = re.sub(r'(.)\1{4,}', r'\1\1', text)
                    
                    # 2. Aynı kelimenin arka arkaya 3 kereden fazla tekrarı (örn: İyiyim. İyiyim. İyiyim. -> İyiyim.)
                    text = re.sub(r'\b(\w+)(?:[\s.,]+\1\b){3,}', r'\1', text, flags=re.IGNORECASE)
                    
                    # 3. İmkansız konuşma hızı (Çok kısa seste çok fazla kelime uydurması)
                    if len(text) > (audio_duration * 40):
                        text = "" # Çöpe at!
                        
                    # 4. Yetersiz Net Konuşma Süresi (Kullanıcının önerisi: "Net Konuşma 40'dan az olmaz")
                    net_speech_duration = max(0.0, audio_duration - silence_duration)
                    if net_speech_duration < 0.4 and len(text) < 20:
                        text = "" # Çöpe at! Arkaplandaki anlık bir tıkırtı veya video sesidir.
                        
                    # 5. Spesifik Halüsinasyon Temizleyici (Sıklıkla uydurulan kelimeler)
                    _clean_text = text.lower().replace(".", "").replace(",", "").replace("!", "").replace("?", "").strip()
                    if _clean_text in ["ııı", "sağol", "ııı sağol", "sağ ol", "teşekkürler", "tamam"]:
                        # Eğer bu kelimeler tek başına ve çok kısa bir sürede söylenmişse halüsinasyondur
                        if audio_duration < 1.5:
                            text = ""


                infer_duration = (
                    time.perf_counter()
                    - start_time
                )
                
                last_infer_duration = infer_duration

                # =================================================
                # OUTPUT
                # =================================================

                if text:

                    if sentence_ended:

                        # -----------------------------------------
                        # FINAL
                        # -----------------------------------------

                        # Önce canlı satırı temizle
                        sys.stdout.write(
                            "\r"
                            + (" " * 250)
                            + "\r"
                        )

                        finish_time = time.perf_counter()
                        net_speech_duration = max(0.1, audio_duration - silence_duration)
                        if speech_start_time is not None:
                            total_e2e_duration = finish_time - speech_start_time
                        else:
                            total_e2e_duration = audio_duration + infer_duration

                        sys.stdout.write(
                            f"[{time.strftime('%X')}] "
                            f"(FINAL) {text} "
                            f"[Net Konuşma: {net_speech_duration:.2f}sn | "
                            f"Model: {infer_duration:.2f}sn | "
                            f"Baştan Sona: {total_e2e_duration:.2f}sn | "
                            f"Toplam Ses: {audio_duration:.2f}sn]\n"
                        )

                        sys.stdout.flush()

                        # Modele giden düzeltilmiş/normalize edilmiş son sesi kaydet ve sakla (D tusu ile dinlenebilir)
                        last_final_audio = audio_to_process.copy()
                        wav_filename = os.path.join(debug_audio_dir, f"final_{time.strftime('%H%M%S')}.wav")
                        with wave.open(wav_filename, 'w') as wf:
                            wf.setnchannels(1)
                            wf.setsampwidth(2)
                            wf.setframerate(RATE)
                            wf.writeframes((np.clip(audio_to_process, -1.0, 1.0) * 32767).astype(np.int16).tobytes())

                        # Context'e ekle (Sadece son 500 karakteri tutarak belleği şişmesini önle)
                        context_text += (
                            " " + text
                        )
                        if len(context_text) > 500:
                            context_text = context_text[-500:]
                            
                        # Son konuşma zamanını güncelle
                        last_speech_time = time.perf_counter()

                    else:

                        # -----------------------------------------
                        # LIVE
                        # -----------------------------------------

                        # Sadece değiştiyse yeniden yaz
                        if text != last_live_text:

                            sys.stdout.write(
                                "\r"
                                + (" " * 250)
                                + "\r"
                            )

                            # Ekrana taşmasını ve silinememesini engellemek için metni kısalt (70 karakter)
                            display_text = text if len(text) <= 70 else text[:67] + "..."

                            sys.stdout.write(
                                f"[{time.strftime('%X')}] "
                                f"(...) {display_text} "
                                f"[{infer_duration:.2f}sn]"
                            )

                            sys.stdout.flush()

                            last_live_text = text

                else:
                    
                    # Eğer whisper hiçbir şey bulamadıysa (örneğin sadece nefes sesi veya klavye sesi yüzünden elendiyse)
                    # ve ekranda kalan bir canlı yazı varsa, onu gizlice SİLMEK yerine, kullanıcının görebilmesi için (BOŞ/ELENDİ) olarak yazdırıyoruz.
                    if sentence_ended:
                        if last_live_text != "":
                            
                            sys.stdout.write(
                                "\r"
                                + (" " * 250)
                                + "\r"
                            )
                            
                            # İptal edilen yazıyı da çok uzunsa kısaltarak göster
                            display_text = last_live_text if len(last_live_text) <= 70 else last_live_text[:67] + "..."
                            
                            sys.stdout.write(
                                f"[{time.strftime('%X')}] "
                                f"(ELENDİ) '{display_text}' (Yeterli ses/kelime bulunamadı)\n"
                            )
                            
                            sys.stdout.flush()
                            
                            last_live_text = ""

                # =================================================
                # FINAL -> TAM RESET
                # =================================================

                if sentence_ended:

                    accumulated_audio = np.array(
                        [],
                        dtype=np.float32
                    )

                    silence_duration = 0.0

                    last_transcribe_time = 0.0

                    last_live_text = ""
                    
                    speech_start_time = None

    except KeyboardInterrupt:

        sys.stdout.write(
            "\r"
            + (" " * 180)
            + "\r"
        )

        print(
            "Kullanıcı tarafından durduruldu."
        )

    except Exception as e:

        print(
            f"\nHATA OLUŞTU: {e}"
        )

    finally:

        print(
            "Kapatıldı."
        )


if __name__ == "__main__":
    main()