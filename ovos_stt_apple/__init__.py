import atexit
import os
import platform
import tempfile
import wave

from ovos_plugin_manager.templates.stt import STT
from ovos_utils.log import LOG

from ovos_stt_apple.build import default_helper_path, ensure_helper
from ovos_stt_apple.helper import HelperError, PersistentHelper

_LANG_DEFAULTS = {
    "nl": "nl-NL", "en": "en-US", "de": "de-DE", "fr": "fr-FR", "es": "es-ES",
    "it": "it-IT", "pt": "pt-PT", "sv": "sv-SE", "da": "da-DK", "nb": "nb-NO",
    "no": "nb-NO", "pl": "pl-PL", "ru": "ru-RU", "ja": "ja-JP", "ko": "ko-KR",
    "zh": "zh-CN", "tr": "tr-TR", "fi": "fi-FI", "cs": "cs-CZ",
}

_FALLBACK_LANGS = {
    "nl-NL", "nl-BE", "en-US", "en-GB", "en-AU", "en-CA", "de-DE", "fr-FR",
    "es-ES", "it-IT", "pt-PT", "pt-BR", "sv-SE", "da-DK", "nb-NO", "pl-PL",
    "ru-RU", "ja-JP", "ko-KR", "zh-CN", "tr-TR", "fi-FI", "cs-CZ",
}


def normalize_lang(lang: str, default: str = "en-US") -> str:
    if not lang:
        return default
    lang = lang.replace("_", "-")
    if "-" in lang:
        base, region = lang.split("-", 1)
        return f"{base.lower()}-{region.upper()}"
    return _LANG_DEFAULTS.get(lang.lower(), default)


class AppleSTT(STT):
    """STT using Apple's Speech framework through a native Swift helper.

    Config keys (all optional):
      helper_path    path to apple-stt-helper (default: built automatically)
      auto_build     compile the helper when missing (default True)
      allow_server   permit Apple servers when no on-device model (default True)
      prefer_on_device  try on-device first, fall back to server (default True)
      persistent     keep one helper process alive (default True)
      pad_ms         silence appended to audio, avoids a cut-off last word (default 500)
      timeout        per-request timeout in seconds (default 30)
    """

    def __init__(self, config=None):
        super().__init__(config)
        if platform.system() != "Darwin":
            raise RuntimeError("ovos-stt-plugin-apple only works on macOS")
        cfg = self.config
        self.allow_server = cfg.get("allow_server", True)
        self.prefer_on_device = cfg.get("prefer_on_device", True)
        self.persistent = cfg.get("persistent", True)
        self.pad_ms = int(cfg.get("pad_ms", 500))
        self.timeout = float(cfg.get("timeout", 30))
        self.helper_path = self._resolve_helper(cfg)
        self._helper = PersistentHelper(self.helper_path) if self.persistent else None
        self._langs = None
        atexit.register(self.shutdown)

    # -- helper resolution ---------------------------------------------------
    def _resolve_helper(self, cfg) -> str:
        path = os.path.expanduser(cfg.get("helper_path") or default_helper_path())
        if os.path.isfile(path):
            return path
        if cfg.get("auto_build", True):
            LOG.info("apple-stt-helper not found, downloading or building it")
            return ensure_helper(path)
        raise FileNotFoundError(
            f"apple-stt-helper not found at {path}; run `ovos-stt-apple-build`")

    # -- audio ---------------------------------------------------------------
    def _write_wav(self, audio) -> str:
        pcm = audio.get_raw_data(convert_rate=16000, convert_width=2)
        pcm += b"\x00" * (int(16000 * 2 * self.pad_ms / 1000))
        f = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        f.close()
        with wave.open(f.name, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(pcm)
        return f.name

    # -- recognition ---------------------------------------------------------
    def execute(self, audio, language=None):
        lang = normalize_lang(language or self.lang)
        path = self._write_wav(audio)
        try:
            modes = [False]  # on-device
            if self.allow_server:
                modes = [False, True] if self.prefer_on_device else [True]
            elif not self.prefer_on_device:
                modes = [False]
            last_err = None
            for server in modes:
                text, err, code = self._recognize(path, lang, server)
                if err is None:
                    return text
                last_err = err
                if code != 4:  # only fall through when no on-device model
                    break
            LOG.error(f"Apple STT failed: {last_err}")
            return ""
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def _recognize(self, path, lang, server):
        if self._helper is not None:
            try:
                msg = self._helper.request(
                    {"cmd": "recognize", "file": path, "lang": lang,
                     "server": server, "timeout": self.timeout},
                    timeout=self.timeout)
            except HelperError as e:
                return "", str(e), 1
            if "error" in msg:
                return "", msg["error"], msg.get("code", 1)
            return msg.get("text", "").strip(), None, 0
        return self._recognize_oneshot(path, lang, server)

    def _recognize_oneshot(self, path, lang, server):
        import subprocess
        cmd = [self.helper_path, "--lang", lang, "--file", path]
        if server:
            cmd.append("--server")
        try:
            out = subprocess.run(cmd, capture_output=True, text=True,
                                 timeout=self.timeout + 5)
        except subprocess.TimeoutExpired:
            return "", "helper timeout", 6
        if out.returncode != 0:
            return "", out.stderr.strip().splitlines()[-1] if out.stderr.strip() \
                else f"exit {out.returncode}", out.returncode
        return out.stdout.strip(), None, 0

    # -- misc ----------------------------------------------------------------
    @property
    def available_languages(self) -> set:
        if self._langs is None:
            try:
                if self._helper is None:
                    raise HelperError("no persistent helper")
                msg = self._helper.request({"cmd": "languages"}, timeout=15)
                self._langs = {l["id"] for l in msg.get("languages", [])}
            except Exception as e:  # noqa
                LOG.debug(f"could not query languages: {e}")
                return _FALLBACK_LANGS
        return self._langs or _FALLBACK_LANGS

    def shutdown(self):
        if self._helper is not None:
            self._helper.close()


AppleSTTConfig = {
    lang: [{
        "lang": lang,
        "display_name": f"Apple Speech ({lang})",
        "offline": False,
        "priority": 60,
    }]
    for lang in sorted(_FALLBACK_LANGS)
}
