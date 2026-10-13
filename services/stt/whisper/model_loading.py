"""Load the same model with progressively more compatible compute modes."""
import logging
import os

from faster_whisper import WhisperModel

logger = logging.getLogger(__name__)


def load_whisper_robust(model_path, device="cuda", compute_type="int8_float16"):
    """Return (model, active device, active compute type).

    Try the requested compute type first. An explicit CPU selection never tries
    CUDA. Let CTranslate2 probe CUDA directly, without requiring PyTorch.
    """
    if device == "cpu":
        compute_type = "int8"
    elif compute_type not in ("int8_float16", "float16", "int8"):
        compute_type = "int8_float16"
    configs = []
    if device != "cpu":
        configs.extend(("cuda", mode) for mode in
                       (compute_type, "int8_float16", "float16", "int8"))
    else:
        configs.append(("cpu", compute_type))
    configs.append(("cpu", "int8"))
    last_error = None
    for active_device, active_compute in dict.fromkeys(configs):
        try:
            logger.info("Loading Whisper %s on %s (%s)",
                        model_path, active_device, active_compute)
            model = WhisperModel(
                model_path,
                device=active_device,
                compute_type=active_compute,
                cpu_threads=(os.cpu_count() or 4) if active_device == "cpu" else 0,
            )
            logger.info("Whisper ready on %s (%s)", active_device, active_compute)
            return model, active_device, active_compute
        except Exception as error:
            last_error = error
            logger.warning("Whisper load failed on %s (%s): %s",
                           active_device, active_compute, error)
    raise RuntimeError("Whisper could not be loaded in any compute mode") from last_error
