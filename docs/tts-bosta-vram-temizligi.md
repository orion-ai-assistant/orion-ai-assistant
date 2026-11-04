# TTS: "boşta VRAM temizle" kullanılmıyor

Karar (2026-11-04, kullanıcı): `services/tts/api.py` içindeki boşta VRAM temizleme işçisi
(`idle_cleanup_mins`, `engine.cleanup()`) kullanılmayacak, üzerinde çalışılmayacak.

- İşçi yalnızca `is_cuda_available()` (PyTorch) doğruysa çalışır. GGUF motorlarında (OmniVoice, VoxCPM2)
  PyTorch yoktur, bu yüzden Vulkan/CUDA/CPU fark etmeksizin zaten hiçbir şey temizlemez.
- Çok işe yaramadığı için düzeltilmeyecek. Boşta belleği boşaltmak isteniyorsa yol, Bileşenler'den motoru
  durdurmaktır (ya da motor başına "boşta durdur" ayrı bir iş olarak ele alınır).
- `/v1/model_info` içindeki `low_vram` ve `idle_cleanup_mins` alanları bu yüzden bilgi amaçlıdır.
