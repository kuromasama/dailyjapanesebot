import google.generativeai as genai
import requests
import os
import json
import random
import re
from datetime import datetime, timedelta
import time

# ================= 環境變數 =================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")

# 檔案設定
VOCAB_FILE = "vocab.json"
USER_DATA_FILE = "user_data.json"
MODEL_NAME = 'models/gemini-2.5-flash' 

# 安全設定
SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
]

# ================= 檔案存取工具 =================

def load_json(filename, default_content):
    if os.path.exists(filename):
        try:
            with open(filename, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and isinstance(default_content, dict):
                    for k, v in default_content.items():
                        if k not in data: data[k] = v
                return data
        except: return default_content
    return default_content

def save_json(filename, data):
    if filename == USER_DATA_FILE and "translation_log" in data:
        if len(data["translation_log"]) > 30:
            data["translation_log"] = data["translation_log"][-30:]
            
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def send_telegram(message):
    if not TG_BOT_TOKEN: print(f"[模擬發送] {message[:50]}..."); return
    if not message: return

    clean_msg = message.replace("**", "").replace("##", "").replace("__", "")
    clean_msg = re.sub(r'<br\s*/?>', '\n', clean_msg)
    
    try:
        requests.post(f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage", json={
            "chat_id": TG_CHAT_ID, "text": clean_msg
        })
    except Exception as e: print(f"TG 發送失敗: {e}")

def normalize_text(text):
    if not text: return ""
    return text.strip().replace("　", " ").lower()

# ================= AI 核心 =================

def ai_correction(user_text, translation_history):
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(MODEL_NAME)
    
    print(f"🤖 AI 正在批改 (合併後長度 {len(user_text)}): {user_text[:20]}...")
    history_str = "\n".join(translation_history[-10:]) if translation_history else "(尚無歷史紀錄)"
    
    prompt = f"""
    使用者正在練習日文，這是她剛剛傳來的內容（可能包含多則訊息的合併）：
    「{user_text}」
    
    【歷史紀錄】
    {history_str}
    
    請扮演日文教授完成批改：
    1. **📈 進度評估**：比較歷史紀錄，判斷是否有進步？給予鼓勵或警惕。
    2. **🎯 批改**：請針對上述內容進行批改，修正錯誤 (✅/❌)。
    3. **✨ 三種多樣化表達**：
       - 👔 正式
       - 🍻 口語
       - 🔄 換句話說
    
    【格式嚴格要求】
    1. **語言**：解說與評語請全程使用「繁體中文」(Traditional Chinese)。
    2. **排版**：
       - **嚴禁** 使用 Markdown 標題 (如 # 或 ##)。
       - 請使用 Emoji (如 📈, 🎯, ✨, 👔, 🍻, 🔄, ✅, ❌) 來區隔段落與項目。
       - **嚴禁** 使用 HTML 標籤 (如 <br>)，請直接換行。
    """
    
    try:
        response = model.generate_content(prompt, safety_settings=SAFETY_SETTINGS)
        return response.text if response.text else "⚠️ AI 批改失敗"
    except Exception as e:
        return f"⚠️ AI 批改錯誤: {e}"

# ================= 邏輯核心 =================

def process_data():
    print("📥 開始處理資料...")
    
    vocab_data = load_json(VOCAB_FILE, {"words": []})
    user_data = load_json(USER_DATA_FILE, {
        "stats": {
            "last_active": "2000-01-01", 
            "streak_days": 0,
            "execution_count": 0,
            "last_quiz_date": "2000-01-01",
            "last_quiz_questions_count": 0,
            "daily_answers_count": 0,       # 今日必修回答數
            "bonus_answers_count": 0,       # 今日 Bonus 回答數
            "yesterday_main_score": 0,
            "yesterday_bonus_score": 0,
            "last_update_id": 0
        },
        "pending_answers": "",
        "translation_log": []
    })
    
    # 初始化防呆
    stats = user_data["stats"]
    if "daily_answers_count" not in stats: stats["daily_answers_count"] = 0
    if "bonus_answers_count" not in stats: stats["bonus_answers_count"] = 0
    if "yesterday_main_score" not in stats: stats["yesterday_main_score"] = 0
    if "yesterday_bonus_score" not in stats: stats["yesterday_bonus_score"] = 0
    if "execution_count" not in stats: stats["execution_count"] = 0
    if "streak_days" not in stats: stats["streak_days"] = 0
    if "last_update_id" not in stats: stats["last_update_id"] = 0

    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/getUpdates"
    
    try:
        response = requests.get(url).json()
        if "result" not in response: return vocab_data, user_data
        
        is_updated = False
        updates_log = []
        correction_msgs = []
        
        today_str = str(datetime.now().date())
        today_answers_detected = 0
        
        pending_correction_texts = []
        
        last_processed_id = user_data["stats"]["last_update_id"]
        is_fresh_start = (last_processed_id == 0)
        max_id_in_this_run = last_processed_id

        for item in response["result"]:
            current_update_id = item["update_id"]
            if current_update_id <= last_processed_id: continue
            if current_update_id > max_id_in_this_run: max_id_in_this_run = current_update_id

            if str(item["message"]["chat"]["id"]) != str(TG_CHAT_ID): continue
            
            msg_time = datetime.fromtimestamp(item["message"]["date"])
            if datetime.now() - msg_time > timedelta(hours=24): continue
            
            text = item["message"].get("text", "").strip()
            if not text: continue

            # Case A: JSON 匯入
            if text.startswith("["):
                try:
                    imported = json.loads(text)
                    if isinstance(imported, list):
                        added = 0
                        for word in imported:
                            if "kanji" not in word: continue
                            kanji = word.get("kanji")
                            if not any(normalize_text(w["kanji"]) == normalize_text(kanji) for w in vocab_data["words"]):
                                vocab_data["words"].append({
                                    "kanji": kanji, "kana": word.get("kana", ""),
                                    "meaning": word.get("meaning", ""),
                                    "count": 1, "added_date": today_str
                                })
                                added += 1
                                is_updated = True
                        updates_log.append(f"📂 匯入 {added} 個新單字")
                except: pass
                continue

            # Case B: 存單字
            match = re.search(r"^([^/\s]+)(?:[ \u3000]+|/)([^/\s]+)(?:[ \u3000]+|/)(.+)$", text)
            if match:
                if is_fresh_start: continue
                kanji, kana, meaning = match.groups()
                if not kanji.lower().startswith("part") and len(text) < 50: 
                    found = False
                    for word in vocab_data["words"]:
                        if normalize_text(word["kanji"]) == normalize_text(kanji):
                            word["count"] += 1 
                            updates_log.append(f"🔄 強化記憶：{kanji}")
                            found = True
                            is_updated = True
                            break
                    if not found:
                        vocab_data["words"].append({
                            "kanji": kanji, "kana": kana, "meaning": meaning, 
                            "count": 1, "added_date": today_str
                        })
                        updates_log.append(f"✅ 收錄：{kanji}")
                        is_updated = True
                    continue

            # Case C: 翻譯/作業
            if not text.startswith("/"):
                if is_fresh_start: continue
                lines_count = len([l for l in text.split('\n') if len(l.strip()) > 1])
                lines_count = max(1, lines_count)
                today_answers_detected += lines_count
                
                pending_correction_texts.append(text)
                user_data["translation_log"].append(f"{today_str}: {text[:50]}")
                is_updated = True

        # === 批改處理 ===
        if is_fresh_start:
            if max_id_in_this_run > user_data["stats"]["last_update_id"]:
                user_data["stats"]["last_update_id"] = max_id_in_this_run
                is_updated = True
            print("🚀 初始化完成：忽略舊有訊息。")
        else:
            if pending_correction_texts:
                combined_text = "\n\n".join(pending_correction_texts)
                history_context = user_data["translation_log"][:-len(pending_correction_texts)]
                result = ai_correction(combined_text, history_context)
                
                title_text = f"📝 **作業/練習批改 (共 {len(pending_correction_texts)} 則合併)：**" if len(pending_correction_texts) > 1 else "📝 **作業/練習批改：**"
                correction_msgs.append(f"{title_text}\n{result}")

            if max_id_in_this_run > user_data["stats"]["last_update_id"]:
                user_data["stats"]["last_update_id"] = max_id_in_this_run
                is_updated = True

        # === 計分邏輯 (必修 vs Bonus) ===
        if today_answers_detected > 0:
            current_main = user_data["stats"]["daily_answers_count"]
            main_quota = 10 
            remaining_quota = max(0, main_quota - current_main)
            
            fill_main = min(today_answers_detected, remaining_quota)
            user_data["stats"]["daily_answers_count"] += fill_main
            
            spill_to_bonus = today_answers_detected - fill_main
            if spill_to_bonus > 0:
                user_data["stats"]["bonus_answers_count"] += spill_to_bonus
            is_updated = True

        # Streak 更新
        if user_data["stats"]["last_active"] != today_str:
            if today_answers_detected > 0 or is_updated:
                 yesterday = str((datetime.now() - timedelta(days=1)).date())
                 if user_data["stats"]["last_active"] == yesterday:
                     user_data["stats"]["streak_days"] += 1
                 else:
                     user_data["stats"]["streak_days"] = 1
                 user_data["stats"]["last_active"] = today_str
                 is_updated = True

        if updates_log: send_telegram("\n".join(set(updates_log)))
        for msg in correction_msgs:
            send_telegram(msg)
            time.sleep(1)

        return vocab_data, user_data

    except Exception as e:
        print(f"Error: {e}")
        return load_json(VOCAB_FILE, {}), load_json(USER_DATA_FILE, {})

# ================= 每日特訓生成 (Bonus 邏輯) =================

def run_daily_quiz(vocab, user):
    if not vocab.get("words"):
        send_telegram("📭 單字庫空的！請傳送單字或匯入 JSON。")
        return user
    
    # ✅ 1. 無論是每日必修還是 Bonus，只要有 Pending Answer，先發送！
    pending_answers = user.get("pending_answers", "")
    if pending_answers:
        send_telegram(f"🗝️ **前次測驗詳解**\n\n{pending_answers}")
        time.sleep(3)
        user["pending_answers"] = ""
    
    today_str = str(datetime.now().date())
    is_new_day = (user["stats"]["last_quiz_date"] != today_str)

    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(MODEL_NAME)
    
    weights = [w.get("count", 1) * 5 for w in vocab["words"]]
    k = min(10, len(vocab["words"]))
    selected_words = random.choices(vocab["words"], weights=weights, k=k)
    word_list = "\n".join([f"{w['kanji']} ({w['meaning']})" for w in selected_words])

    # ================= Scenario A: 新的一天 (每日必修) =================
    if is_new_day:
        # 結算昨天
        user["stats"]["yesterday_main_score"] = user["stats"]["daily_answers_count"]
        user["stats"]["yesterday_bonus_score"] = user["stats"]["bonus_answers_count"]
        
        # 重置今日
        user["stats"]["daily_answers_count"] = 0
        user["stats"]["bonus_answers_count"] = 0
        
        # 增加次數
        user["stats"]["execution_count"] += 1
        exec_count = user["stats"]["execution_count"]
        streak_days = user["stats"]["streak_days"]
        
        # 情緒 Prompt
        is_first_run = (user["stats"]["last_quiz_date"] == "2000-01-01") or (exec_count == 1)
        main_score = user["stats"]["yesterday_main_score"]
        bonus_score = user["stats"]["yesterday_bonus_score"]
        
        emotion_prompt = ""
        if is_first_run:
            emotion_prompt = "這是第一次見面 (Day 1)。請熱情歡迎，介紹模式：每日出題，下次給解答。"
        else:
            answer_rate = main_score / 10
            if answer_rate >= 0.8:
                if bonus_score > 0:
                    emotion_prompt = f"昨日表現：必修 {main_score}/10，Bonus {bonus_score}。狀態：神一般的自律！請用極度崇拜語氣誇獎！"
                else:
                    emotion_prompt = f"昨日表現：必修 {main_score}/10。狀態：優秀。"
            elif answer_rate >= 0.3:
                emotion_prompt = f"昨日表現：必修 {main_score}/10。狀態：尚可。提醒要更努力。"
            else:
                emotion_prompt = f"昨日表現：必修 {main_score}/10。狀態：偷懶！開啟【幽默情勒模式】。"

        print("🤖 生成每日必修 (10題)...")
        prompt = f"""
        你是日文 N2 斯巴達教練。
        這是第 {exec_count} 次特訓 (Day {streak_days})。
        
        【情緒設定】
        {emotion_prompt}
        
        【今日單字】
        {word_list}
        
        請製作 **10 題** 翻譯測驗 (7題中翻日，3題日翻中)。
        
        【輸出格式】
        1. Part 1: 題目卷 (繁體中文+Emoji，無HTML，無答案)。
        2. 分隔線 `|||SEPARATOR|||`
        3. Part 2: 解答卷 (下次發送)。
        """
        
        try:
            response = model.generate_content(prompt, safety_settings=SAFETY_SETTINGS)
            if response.text and "|||SEPARATOR|||" in response.text:
                parts = response.text.split("|||SEPARATOR|||")
                send_telegram(parts[0].strip())
                user["pending_answers"] = parts[1].strip() # 存入解答
                
                user["stats"]["last_quiz_date"] = today_str
                user["stats"]["last_quiz_questions_count"] = 10
        except Exception as e:
            print(f"Error: {e}")
            send_telegram("⚠️ 測驗生成失敗")

    # ================= Scenario B: 同一天再次執行 (Bonus 模式) =================
    else:
        print("🤖 生成 Bonus 挑戰 (3題)...")
        
        prompt = f"""
        你是日文 N2 斯巴達教練。
        使用者今天已經領過每日作業了，但她**主動**再次執行程式，表示她想要更多練習！
        
        請用「驚喜、讚嘆」的語氣，稱讚她的積極度。
        並提供 **3 題** 高難度的 N2 翻譯挑戰 (Bonus Challenge)。
        
        【今日單字】
        {word_list}
        
        【輸出格式】
        1. Part 1: Bonus 題目卷 (繁體中文+Emoji，無HTML，無答案)。
           標題請寫：⚔️ **Bonus 無限挑戰** ⚔️
        2. 分隔線 `|||SEPARATOR|||`
        3. Part 2: 解答卷 (下次發送)。
        """

        try:
            response = model.generate_content(prompt, safety_settings=SAFETY_SETTINGS)
            if response.text and "|||SEPARATOR|||" in response.text:
                parts = response.text.split("|||SEPARATOR|||")
                send_telegram(parts[0].strip())
                
                # ✅ 關鍵：直接存入，下次執行時會被上面的邏輯發送
                # 如果使用者連續跑多次 Bonus，這裡會覆蓋掉上一個 Bonus 的解答
                # 這是正確的，因為使用者應該要先看到上一個 Bonus 的解答，再領新的 Bonus 題目
                user["pending_answers"] = parts[1].strip() 
                
        except Exception as e:
            print(f"Error: {e}")
            send_telegram("⚠️ Bonus 生成失敗")

    return user

if __name__ == "__main__":
    v_data, u_data = process_data()
    u_data_updated = run_daily_quiz(v_data, u_data)
    
    save_json(VOCAB_FILE, v_data)
    if u_data_updated:
        save_json(USER_DATA_FILE, u_data_updated)
    else:
        save_json(USER_DATA_FILE, u_data)