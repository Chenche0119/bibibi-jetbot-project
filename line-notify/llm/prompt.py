"""LLM 轉譯的提示詞與輸出檢查。

    notice ─► build_messages() ─► 模型 ─► compose() ─► check_output() ─► 通過才用，否則退回模板

提示詞只給事實（machine.notice_facts），模型只負責把事實寫成句子；要不要通知由狀態機決定。
小模型容易寫出不合規定的句子，所以產生後一定要經過 check_output()，任何一條沒過就退回模板。
"""

import collections
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state_machine"))

from machine import (MESSAGE_HEADER, READING_TEMPLATES, RECOVERY,  # noqa: E402
                     format_clock, format_duration, is_following, notice_facts)

# ═══════════════════════════════════════════════════════════
#  設定區
# ═══════════════════════════════════════════════════════════

MAX_CHARS = 120        # 訊息本文（不含開頭的【伴護機器人】HH:MM）最多幾字，不算空白與換行

# 檢查規則：(代號, 說明)。check_output() 回傳沒通過的代號清單
RULES = [
    ("empty", "回應不可為空"),
    ("traditional", "繁體中文、台灣用語，不可出現簡體字"),
    ("length", "本文不超過 {} 字".format(MAX_CHARS)),
    ("no_fields", "不得出現欄位名稱或 JSON"),
    ("no_english", "不得出現英文或提示詞殘留"),
    ("numbers", "數值必須來自 notice"),
    ("no_medical", "不得給醫療建議或推測身體狀況"),
    ("header", "以【伴護機器人】HH:MM 開頭"),
    ("consistent", "提到的狀況與 notice 相符（自行加的規則）"),
    ("duration_semantics", "持續時間不可寫成「多久以前」（任務六加的規則）"),
]

# 常見的簡體字（只收錄在台灣正體中文不會出現的字；「后、于、里、着、据、系」等兩岸都用的字不收）
SIMPLIFIED_CHARS = set(
    "车机时间连线复这个们没发现断离检测应该请为从进动过续约钟电话让说还会经见问题处对开关长边远"
    "办务员实际当数网络视频讯护伤医疗药险碍转绕状态况认识报与无专业东两头样气点广难显属亲爱语读写"
    "统计设备录帮带导结继给较虑随踪丢寻标户门场岁儿静稳须确刚变换试获严紧马联传输号码听声灯闪响觉"
    "触墙挡体书级么吗"
)
# 大陸用語（字是繁體，但台灣不這樣說）
MAINLAND_TERMS = ["信息", "視頻", "網絡", "屏幕", "質量", "軟件", "默認", "反饋", "攝像頭", "激光", "鏈接", "登錄"]

# 車上與 notice 的欄位、代號：出現在訊息裡代表模型把原始資料照抄出來
FIELD_NAMES = [
    "category", "kind", "time", "since", "duration", "car", "readings", "others", "state", "events",
    "has_target", "distance_m", "blocked_prob", "speed_scale", "target_h", "car_ts",
    "offline", "impassable", "no_target", "alert", "reminder", "recovery",
    "waiting", "confirming", "locked", "detour", "impassable_wait", "null", "true", "false",
]
JSON_CHARS = "{}[]\"`"

# 提示詞殘留：小模型常把範例的標籤、角色名稱或特殊記號一起吐出來
RESIDUE_MARKERS = ["事實：", "事實:", "訊息：", "訊息:", "範例", "輸出：", "輸入：", "<|", "|>", "###"]

# 醫療建議或推測身體狀況：notice 只有車子看到什麼，沒有任何健康資訊
MEDICAL_TERMS = ["送醫", "就醫", "就診", "醫院", "急診", "救護車", "醫生", "醫師", "看醫", "服藥", "吃藥",
                 "用藥", "藥物", "診斷", "病情", "症狀", "受傷", "跌倒", "昏倒", "暈倒", "不適", "生命危險"]

# 事實一致：每個類別要用到的字眼。notice 有的類別至少要提到一個；
# 失聯的字眼另外不可出現在沒有失聯的訊息裡（小模型常把「恢復」都寫成連線恢復；
# 2026-10-01 評測：看不到受護者寫成「失去受護者的訊號，約 3 秒沒有收到資料」）
CATEGORY_WORDS = {
    "offline": ["連線", "斷線", "失聯", "訊號", "收到資料", "收不到資料"],
    "impassable": ["通過", "擋", "障礙", "卡住", "繞", "停在原地"],
    "no_target": ["受護者"],
}
ONLY_WHEN_PRESENT = ["offline"]

# 時間語意：notice 的時間都是「持續多久」，寫成「N 秒前」「N 分鐘前就」意思就錯了
# （2026-09-29 實測：「約 7 秒前就被擋住了」「約 13 秒前就已經重新連上」）
TIME_AGO_RE = re.compile(r"(秒|分鐘|分半|小時)\s*(之)?前")

# 不給持續時間的通知：看不到受護者的「發生」固定在畫面無人 3 秒時發出，模板也不寫時間。
# 給了模型會硬把「約 3 秒」寫進去，評測時寫成「約 3 秒沒有收到資料」，像是失聯
NO_DURATION = [("no_target", "alert")]

# ── 提示詞 ──
SYSTEM_PROMPT = """你是陪伴長者的「伴護機器人」，負責把車子回報的狀況寫成 LINE 訊息，傳給家屬。

語氣：平實、簡短、口語，像家人之間報平安；不誇張、不製造恐慌。

你會收到一段 JSON，是這則通知的事實：
- category：
  offline＝與機器人失去連線，暫時無法得知車子的狀況（不要說車子照常運作）；
  impassable＝前方被擋住、繞不過去，車子停在原地；
  no_target＝車子看不到受護者，停在原地等受護者回到前方（車子不會自己去找，不要說正在搜尋）
- kind：alert＝剛發生；reminder＝仍在持續；recovery＝已經解除
- duration_text：這個狀況「持續了多久」（已經換算好，照抄即可；沒有這個欄位就不要寫時間）。
  只能寫成「已經約 N 秒」「停了約 N 秒」「中斷了約 N 秒」，不可寫成「約 N 秒前」
- following：只有 impassable 解除時才有。true＝車子繼續跟隨；false＝車子沒有鎖定受護者，停在原地等受護者回到前方
- readings.distance_m：車子與前方障礙的距離（公尺），寫成「約 X.X 公尺」；沒有這個欄位就不要寫距離
- others：同一時間發生的其他事，要一起寫進同一段，用「另外，」接在後面

規則（一定要遵守）：
1. 只寫 JSON 裡有的事實。沒有的資訊就不要提，不要猜測原因，不要自己編數字；category 不是 offline 就不要提連線
2. 使用繁體中文、台灣用語，不可使用簡體字
3. 不超過 {max_chars} 字，一到三句話
4. 不可出現英文、欄位名稱（例如 has_target、impassable_wait）或 JSON 符號
5. 不可給醫療建議，不可推測受護者的身體狀況（例如「請立即送醫」「可能跌倒」都不行）
6. 不要寫開頭的時間和稱呼，直接寫內容""".format(max_chars=MAX_CHARS)

# 小模型要給範例：(notice 的事實, 理想訊息本文)。事實的格式與 facts_for_prompt() 的輸出相同
# 理想訊息取自 machine.TEMPLATES（Claude 產生、人工審核，docs/tasks/06-template-upgrade.md），
# 涵蓋發生／提醒／解除、有無距離、卡住解除的兩種分支、others 併入，以及 9/29 LLM 寫錯時間語意的兩種情況
FEW_SHOTS = [
    ({"category": "impassable", "kind": "alert", "readings": {"distance_m": 0.42}, "others": []},
     "車子前方被擋住、繞不過去，已經停在原地等待。障礙移開後會自動恢復。目前車子與前方障礙約 0.4 公尺。"),
    ({"category": "no_target", "kind": "alert", "readings": {}, "others": []},
     "車子看不到受護者了，已經停在原地等待。受護者回到車子正前方站定後，車子會自動重新跟上。"),
    ({"category": "no_target", "kind": "reminder", "duration_text": "約 5 分鐘", "readings": {},
      "others": [{"category": "offline", "kind": "recovery", "duration_text": "約 20 秒"}]},
     "車子到現在還是看不到受護者，已經約 5 分鐘了，仍停在原地等待。另外，與機器人的連線已恢復（中斷約 20 秒）。"),
    ({"category": "impassable", "kind": "recovery", "duration_text": "約 7 秒", "following": True,
      "readings": {}, "others": []},
     "前方已經可以通過，車子繼續跟隨了。剛才停了約 7 秒。"),
    ({"category": "impassable", "kind": "recovery", "duration_text": "約 2 分半", "following": False,
      "readings": {}, "others": []},
     "前方已經可以通過（停了約 2 分半），但車子目前沒有看到受護者，正在原地等受護者回到前方。"),
    ({"category": "offline", "kind": "alert", "duration_text": "約 8 秒", "readings": {}, "others": []},
     "與機器人失去連線，已經約 8 秒沒有收到資料，暫時無法得知車子的狀況。連線恢復後會再通知您。"),
    ({"category": "offline", "kind": "recovery", "duration_text": "約 13 秒", "readings": {}, "others": []},
     "與機器人的連線已經恢復，剛才中斷了約 13 秒。"),
    ({"category": "no_target", "kind": "recovery", "duration_text": "約 2 分鐘", "readings": {}, "others": []},
     "車子已經重新找到受護者，恢復跟隨了。剛才約 2 分鐘沒看到人。"),
]


def facts_for_prompt(notice):
    """給模型看的事實：machine.notice_facts() 整理成小模型好讀的樣子。

    - duration_sec 換成 format_duration() 的文字，模型照抄即可，不用自己算；不到 1 秒（剛發生）時不給
    - Unix 秒數（time、since、car_ts）對模型沒有意義、還可能被抄進訊息，拿掉；時間由固定開頭寫
    - car（車上的 state、has_target、events）拿掉：category、kind 已經說明狀況。
      評測時給了 car，模型在「卡住」的訊息裡因 has_target 為 false 寫出「看不到受護者」，家屬會誤會
    - readings 只留模板也會寫的數值（machine.READING_TEMPLATES，目前只有卡住時的距離）。
      評測時給了全部數值，模型會在「看不到受護者」時寫出距離，家屬會誤以為是與受護者的距離
    提示詞越短，每則要處理的 token 越少，產生越快（本地模型的主要瓶頸）。
    """
    facts = notice_facts(notice)
    out = collections.OrderedDict([("category", facts["category"]), ("kind", facts["kind"])])
    if facts["duration_sec"] >= 1 and (facts["category"], facts["kind"]) not in NO_DURATION:
        out["duration_text"] = format_duration(facts["duration_sec"])
    if facts["category"] == "impassable" and facts["kind"] == RECOVERY:
        # car 整個拿掉，只留這一個判斷：解除後車子是否繼續跟隨（與 machine.template_versions 的分支相同）
        out["following"] = is_following(facts.get("car"))
    readings = facts.get("readings") or {}
    shown = [field for field, _ in READING_TEMPLATES.get(facts["category"], [])] if facts["kind"] != RECOVERY else []
    out["readings"] = {k: v for k, v in readings.items() if k in shown}
    out["others"] = [{"category": o["category"], "kind": o["kind"],
                      "duration_text": format_duration(o["duration_sec"])}
                     for o in facts.get("others", [])]
    return out


def build_messages(notice):
    """組成 chat 格式的訊息清單：系統提示詞 → 範例（一問一答）→ 這則通知的事實。"""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for facts, answer in FEW_SHOTS:
        messages.append({"role": "user", "content": json.dumps(facts, ensure_ascii=False)})
        messages.append({"role": "assistant", "content": answer})
    messages.append({"role": "user", "content": json.dumps(facts_for_prompt(notice), ensure_ascii=False)})
    return messages


# ── 產生後的整理與檢查 ──
HEADER_RE = re.compile(r"^\s*【伴護機器人】\s*\d{1,2}[:：]\d{2}\s*")


def header_for(notice):
    return MESSAGE_HEADER.format(clock=format_clock(notice["time"]))


def compose(raw, notice):
    """把模型的回應整理成完整訊息：去掉頭尾空白、模型自己寫的開頭，再加上固定開頭。

    只做不改變意思的整理；內容不合規定交給 check_output() 判斷，這裡不修補。
    回應為空時回傳空字串。
    """
    body = (raw or "").strip()
    body = HEADER_RE.sub("", body).strip()
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    if not lines:
        return ""
    return "\n".join([header_for(notice)] + lines)


def split_message(text):
    """(開頭, 本文)。沒有換行時本文為空。"""
    parts = text.split("\n", 1)
    return parts[0], (parts[1] if len(parts) > 1 else "")


CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "兩": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9}
CN_NUMBER_RE = re.compile(r"([零〇一二兩三四五六七八九十百]+)\s*(?:個)?\s*(秒|分鐘|分|公尺|公分|小時|次|%|％)")
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


def parse_cn_number(text):
    """解析「三十」「一百二十」這類中文數字（到百位）。無法解析時回傳 None。"""
    total, current = 0, 0
    for ch in text:
        if ch in CN_DIGITS:
            current = CN_DIGITS[ch]
        elif ch == "十":
            total += (current or 1) * 10
            current = 0
        elif ch == "百":
            total += (current or 1) * 100
            current = 0
        else:
            return None
    return total + current


def numbers_in(text):
    """訊息中出現的所有數字（阿拉伯數字，以及後面接單位的中文數字）。"""
    values = [float(m) for m in NUMBER_RE.findall(text)]
    for digits, _unit in CN_NUMBER_RE.findall(text):
        value = parse_cn_number(digits)
        if value is not None:
            values.append(float(value))
    return values


def allowed_numbers(notice):
    """notice 中的事實可以寫成哪些數字。

    持續時間：format_duration() 的寫法（約 N 秒／約 N 分鐘）與精確秒數
    距離：公尺取到小數一位、兩位，或換算成公分
    被擋機率：百分比或小數兩位
    時刻：通知時間、開始時間的時與分
    """
    allowed = set()
    durations = [notice["duration_sec"]] + [o["duration_sec"] for o in notice.get("others", [])]
    for seconds in durations:
        allowed.update(float(n) for n in NUMBER_RE.findall(format_duration(seconds)))
        allowed.add(float(int(round(seconds))))
    readings = notice.get("readings") or {}
    if "distance_m" in readings:
        d = readings["distance_m"]
        allowed.update([round(d, 1), round(d, 2), float(int(round(d * 100)))])
    if "blocked_prob" in readings:
        p = readings["blocked_prob"]
        allowed.update([float(int(round(p * 100))), round(p, 2)])
    for t in (notice.get("time"), notice.get("since")):
        if t is not None:
            allowed.update(float(n) for n in format_clock(t).split(":"))
    return allowed


def check_output(text, notice):
    """檢查一則完整訊息（含開頭）。回傳沒通過的規則代號清單，空清單代表全部通過。"""
    if not text or not text.strip():
        return ["empty"]
    failed = []
    head, body = split_message(text)

    if any(ch in SIMPLIFIED_CHARS for ch in text) or any(term in text for term in MAINLAND_TERMS):
        failed.append("traditional")

    if not body.strip() or len(re.sub(r"\s", "", body)) > MAX_CHARS:
        failed.append("length")

    lowered = text.lower()
    if (any(ch in text for ch in JSON_CHARS) or re.search(r"[a-z]+_[a-z_]+", lowered)
            or any(re.search(r"(?<![a-z]){}(?![a-z])".format(name), lowered) for name in FIELD_NAMES)):
        failed.append("no_fields")

    if re.search(r"[A-Za-z]{2,}", body) or any(marker in text for marker in RESIDUE_MARKERS):
        failed.append("no_english")

    allowed = allowed_numbers(notice)
    if any(not any(abs(value - a) < 1e-6 for a in allowed) for value in numbers_in(body)):
        failed.append("numbers")

    if any(term in text for term in MEDICAL_TERMS):
        failed.append("no_medical")

    if head != header_for(notice) or text.count("【伴護機器人】") != 1:
        failed.append("header")

    present = set([notice["category"]] + [o["category"] for o in notice.get("others", [])])
    if (any(not any(w in body for w in CATEGORY_WORDS[c]) for c in present if c in CATEGORY_WORDS)
            or any(c not in present and any(w in body for w in CATEGORY_WORDS[c]) for c in ONLY_WHEN_PRESENT)):
        failed.append("consistent")

    if TIME_AGO_RE.search(body):
        failed.append("duration_semantics")
    return failed


def unexpected_numbers(text, notice):
    """除錯用：訊息中哪些數字對不上 notice。"""
    allowed = allowed_numbers(notice)
    _, body = split_message(text)
    return [v for v in numbers_in(body) if not any(abs(v - a) < 1e-6 for a in allowed)]
