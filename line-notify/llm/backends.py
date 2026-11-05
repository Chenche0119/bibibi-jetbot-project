"""LLM 後端。三種後端共用同一個介面，切換只靠設定（formatter.py 的 JETBOT_LLM_BACKEND）。

| 後端       | 現況       | 說明 |
|------------|------------|------|
| template   | 已存在     | machine.format_message()，永遠是最後的退路；不需要模型 |
| local      | 預設       | MLX 在本機跑小模型（Apple Silicon 限定） |
| cloud      | 預留       | 只留介面與設定位置，不實作。之後照官方 SDK 寫一個類別即可（見 llm/README.md） |

介面（新增後端時照著寫）：
    backend.load(warmup_messages)           載入，並用 warmup_messages 預熱一次。失敗就丟例外，
                                            formatter 會退回模板並只提示一次
    backend.generate(messages, should_stop) messages 是 chat 格式清單，最後一則是這次的事實、前面是固定的
                                            提示詞與範例；should_stop() 為 True 時要盡快停下
                                            （逾時了，結果會被丟棄）。回傳模型寫出的文字
    backend.info                            dict，載入時間、記憶體等量測數據，給 eval 與 README 用

load() 與 generate() 一律由 formatter 的同一條背景執行緒呼叫，後端不需要自己處理多執行緒。
"""

import json
import platform
import resource
import sys
import time

# ═══════════════════════════════════════════════════════════
#  設定區
# ═══════════════════════════════════════════════════════════

# 40 億參數以下的量化中文指令模型；M1 MacBook Air 8GB，模型檔約 1.7GB
# （2026-09-24 於 Hugging Face 確認：mlx-community/Qwen2.5-3B-Instruct-4bit，model.safetensors 1,736,293,090 bytes）
DEFAULT_LOCAL_MODEL = "mlx-community/Qwen2.5-3B-Instruct-4bit"
MAX_TOKENS = 160       # 120 字的中文約 120～150 個 token，多給一點讓模型自然結束
TEMPERATURE = 0.0      # 貪婪解碼：同一則通知每次產生的文字相同，方便重複評測

# ═══════════════════════════════════════════════════════════


class BackendUnavailable(Exception):
    """後端無法使用（沒安裝套件、平台不支援、模型載入失敗、沒有實作）。訊息會顯示給使用者一次。"""


def _rss_mb():
    """本程序目前為止的最大常駐記憶體（MB）。macOS 的 ru_maxrss 單位是 bytes，Linux 是 KB。"""
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss / (1024.0 * 1024.0) if sys.platform == "darwin" else rss / 1024.0


class TemplateBackend(object):
    """固定模板。不經過模型，formatter 看到 name == "template" 就直接用 machine.format_message()。"""

    name = "template"

    def __init__(self, **kwargs):
        self.info = {}

    def load(self, warmup_messages=None):
        pass

    def generate(self, messages, should_stop):
        raise BackendUnavailable("template 後端不產生文字，直接使用模板")


class LocalMLXBackend(object):
    """MLX 本地模型（mlx-lm）。模型只載入一次，放在這個物件裡重複使用。

    提示詞快取：系統提示詞與範例（messages 除了最後一則）每次都一樣，約占提示詞九成。
    第一次先算好這段的 KV 快取，之後每則只需處理最後一則事實，產生時間從約 9 秒降到 2 秒內（見 README）。

    Qwen3.5（任務七）：
    - 思考模式預設開啟，套用 chat template 時一律傳 enable_thinking=False（Qwen2.5 的樣板會忽略這個參數）
    - 混合架構：Gated DeltaNet 層的快取是遞迴狀態（ArraysCache），不能像注意力層的 KV 快取那樣截斷。
      算完固定前綴時存一份這些層的狀態快照，每次產生前還原；可截斷的層照舊截回固定前綴
    """

    name = "local"

    def __init__(self, model=None, max_tokens=MAX_TOKENS, temperature=TEMPERATURE, prompt_cache=True, **kwargs):
        self.model_name = model or DEFAULT_LOCAL_MODEL
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.use_prompt_cache = prompt_cache
        self.info = {"model": self.model_name}
        self._model = None
        self._tokenizer = None
        self._mlx = None
        self._prefix_key = None      # 目前快取的是哪一段固定前綴
        self._prefix_len = 0
        self._cache = None
        self._snapshot = None        # 不能截斷的層：固定前綴算完時的狀態（可截斷的層為 None）

    def load(self, warmup_messages=None):
        if sys.platform != "darwin" or platform.machine() != "arm64":
            raise BackendUnavailable("本地模型需要 Apple Silicon 的 macOS（目前是 {} {}）".format(
                sys.platform, platform.machine()))
        try:
            import mlx.core as mx
            from mlx_lm import load, stream_generate
            from mlx_lm.models.cache import make_prompt_cache, trim_prompt_cache
            from mlx_lm.sample_utils import make_sampler
        except ImportError as e:
            raise BackendUnavailable("沒有安裝 mlx-lm（{}）。用 uv run --with mlx-lm 執行即可啟用".format(e))

        rss_before = _rss_mb()
        start = time.time()
        try:
            self._model, self._tokenizer = load(self.model_name)
        except Exception as e:
            raise BackendUnavailable("模型 {} 載入失敗：{!r}".format(self.model_name, e))
        self.info["load_sec"] = round(time.time() - start, 2)

        self._mlx = {"mx": mx, "stream_generate": stream_generate, "make_prompt_cache": make_prompt_cache,
                     "trim_prompt_cache": trim_prompt_cache, "sampler": make_sampler(temp=self.temperature)}
        # 預熱：第一次產生要編譯運算核心、建立提示詞快取，先跑一次，第一則通知就不用多等
        start = time.time()
        self.generate(warmup_messages or [{"role": "user", "content": "你好"}], lambda: False)
        self.info["warmup_sec"] = round(time.time() - start, 2)
        self.info["prefix_tokens"] = self._prefix_len
        self.info["rss_mb_before"] = round(rss_before, 1)
        self.info.update(self.memory())

    def memory(self):
        """載入後的記憶體占用：MLX 目前配置的量、高峰，以及整個程序的最大常駐記憶體。"""
        mx = self._mlx["mx"] if self._mlx else None
        out = {"rss_mb": round(_rss_mb(), 1)}
        if mx is not None:
            try:
                out["mlx_active_mb"] = round(mx.get_active_memory() / 1e6, 1)
                out["mlx_peak_mb"] = round(mx.get_peak_memory() / 1e6, 1)
            except AttributeError:   # 舊版 mlx 在 mx.metal 底下
                out["mlx_active_mb"] = round(mx.metal.get_active_memory() / 1e6, 1)
                out["mlx_peak_mb"] = round(mx.metal.get_peak_memory() / 1e6, 1)
        return out

    def _template(self, messages, add_generation_prompt):
        return self._tokenizer.apply_chat_template(
            messages, add_generation_prompt=add_generation_prompt, enable_thinking=False)

    def _prompt(self, messages):
        """回傳 (要送進模型的 token, 可用的快取或 None)。固定前綴已快取時只送後面那段。"""
        tokens = self._template(messages, True)
        if not self.use_prompt_cache or len(messages) < 2:
            return tokens, None
        prefix = messages[:-1]
        key = json.dumps(prefix, ensure_ascii=False, sort_keys=True)
        if key != self._prefix_key:
            # 固定前綴 = 兩則不同提問組出來的完整提示詞的共同開頭。
            # 不直接組 prefix：Qwen3.5 的樣板會在「最後一則 assistant」前加空的思考區塊，
            # 單獨組 prefix 時最後一組範例被當成當前回覆，跟完整提示詞（範例是歷史回覆）對不上
            a = self._template(prefix + [{"role": "user", "content": "甲"}], True)
            b = self._template(prefix + [{"role": "user", "content": "乙"}], True)
            n = 0
            while n < min(len(a), len(b)) and a[n] == b[n]:
                n += 1
            prefix_tokens = list(a[:n])
            if n == 0 or list(tokens[:n]) != prefix_tokens:
                return tokens, None      # 樣板組出來的前綴對不上，就不用快取
            mx = self._mlx["mx"]
            cache = self._mlx["make_prompt_cache"](self._model)
            self._model(mx.array(prefix_tokens)[None], cache=cache)
            mx.eval([c.state for c in cache])
            self._snapshot = [None if c.is_trimmable() else list(c.state) for c in cache]
            self._cache, self._prefix_key, self._prefix_len = cache, key, len(prefix_tokens)
        # 上一次產生的內容還在快取裡：可截斷的層截回固定前綴，遞迴狀態的層還原成快照
        for c, snapshot in zip(self._cache, self._snapshot):
            if snapshot is None:
                extra = c.offset - self._prefix_len
                if extra > 0:
                    c.trim(extra)
            else:
                c.state = list(snapshot)
        return tokens[self._prefix_len:], self._cache

    def generate(self, messages, should_stop, max_tokens=None):
        tokens, cache = self._prompt(messages)
        kwargs = {"max_tokens": max_tokens or self.max_tokens, "sampler": self._mlx["sampler"]}
        if cache is not None:
            kwargs["prompt_cache"] = cache
        pieces = []
        for response in self._mlx["stream_generate"](self._model, self._tokenizer, tokens, **kwargs):
            pieces.append(response.text)
            if should_stop():   # 逾時了，結果反正會被丟棄，提早停下把執行緒讓出來
                break
        return "".join(pieces)


class CloudBackend(object):
    """雲端 API（預留，本次不實作）。

    之後要接時：在 load() 用官方 SDK 建立 client（金鑰從 .env 讀，例如 ANTHROPIC_API_KEY），
    generate() 把 messages 第一則 system 拿出來當系統提示詞、其餘照送，回傳文字。
    逾時、輸出檢查、退回模板都由 formatter 處理，不需要在這裡重做。詳見 llm/README.md。
    """

    name = "cloud"

    def __init__(self, model=None, **kwargs):
        self.model_name = model
        self.info = {"model": model}

    def load(self, warmup_messages=None):
        raise BackendUnavailable("cloud 後端尚未實作（預留位置，見 llm/README.md「未來升級方向」）")

    def generate(self, messages, should_stop):
        raise BackendUnavailable("cloud 後端尚未實作")


BACKENDS = {
    "template": TemplateBackend,
    "local": LocalMLXBackend,
    "cloud": CloudBackend,
}


def make_backend(name, **kwargs):
    """依名稱建立後端。不認得的名稱丟 BackendUnavailable（formatter 會退回模板）。"""
    if name not in BACKENDS:
        raise BackendUnavailable("沒有這個後端：{}（可用：{}）".format(name, "、".join(sorted(BACKENDS))))
    return BACKENDS[name](**kwargs)
