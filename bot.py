import os, json, asyncio
import discord
from discord import app_commands
from dotenv import load_dotenv
import httpx

load_dotenv(dotenv_path=".env")
from food_tool import Tools as FoodTools

DISCORD_TOKEN = os.environ["DISCORD_BOT_TOKEN"]

LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api-gateway.netdb.csie.ncku.edu.tw")
LLM_API_KEY = os.environ.get("LLM_API_KEY", os.environ.get("OPENAI_API_KEY", ""))

food = FoodTools()

# ====== 資料持久化 (Persistence) ======
DATA_FILE = "saved_lists.json"

def load_data():
    """從 JSON 檔案讀取待吃清單"""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            return {}
    return {}

def save_data():
    """將待吃清單寫入 JSON 檔案"""
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(user_saved_lists, f, ensure_ascii=False, indent=2)

# ====== 全域變數 ======
# 上下文記憶 (短期記憶，重啟消失沒關係)
user_contexts = {}

# 待吃清單 (長期記憶，需讀檔)
# 結構: {"user_id_string": [{"name": "店名", "map_link": "連結", "rating": "..."}]}
# 注意: JSON key 必須是字串，所以存取時 user_id 要轉 str
user_saved_lists = load_data()

# ====== UI Components: 按鈕介面 ======
class RestaurantView(discord.ui.View):
    def __init__(self, restaurants: list):
        super().__init__(timeout=None)
        self.restaurants = restaurants
        
        for i, r in enumerate(restaurants):
            emoji_num = ["1️⃣", "2️⃣", "3️⃣"][i] if i < 3 else "🍽️"
            
            btn = discord.ui.Button(
                label=f"加入 {r['name'][:10]}", 
                style=discord.ButtonStyle.primary, 
                emoji=emoji_num,
                custom_id=f"add_btn_{i}_{r['map_link'][-5:]}" # 避免ID重複
            )
            
            # 使用 closure 捕捉當前的 r
            async def callback(interaction: discord.Interaction, restaurant=r):
                user_id = str(interaction.user.id) # 轉字串以符合 JSON key
                
                if user_id not in user_saved_lists:
                    user_saved_lists[user_id] = []
                
                # 檢查是否已存在
                if any(saved['name'] == restaurant['name'] for saved in user_saved_lists[user_id]):
                    await interaction.response.send_message(f"❌ **{restaurant['name']}** 已經在你的清單裡囉！", ephemeral=True)
                else:
                    user_saved_lists[user_id].append({
                        "name": restaurant['name'],
                        "map_link": restaurant['map_link'],
                        "rating": restaurant['rating']
                    })
                    # 立即存檔
                    save_data()
                    await interaction.response.send_message(f"✅ 已將 **{restaurant['name']}** 加入待吃清單！", ephemeral=True)

            btn.callback = callback
            self.add_item(btn)

# ====== LLM & Analysis Logic ======
async def llm_generate(prompt: str) -> str:
    if not LLM_API_KEY: return "❌ LLM_API_KEY 未設定"
    url = LLM_BASE_URL.rstrip("/") + "/api/generate"
    payload = {"model": "gpt-oss:120b", "prompt": prompt, "stream": False}
    headers = {"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"}
    
    async with httpx.AsyncClient(timeout=120.0) as http:
        try:
            resp = await http.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data.get("response", "") or data.get("text", "")
        except Exception as e:
            return f"連線錯誤: {e}"

async def analyze_request(user_text: str, current_context: dict = None) -> dict:
    context_str = "無 (這是新的對話)"
    if current_context:
        context_str = f"地點={current_context.get('location')}, 關鍵字={current_context.get('keyword')}"

    system_prompt = (
        "你是一個意圖分析助手。請根據使用者的輸入以及「目前的對話情境」，回傳 JSON 格式的分析結果。\n"
        "--------------------------------------------------\n"
        f"【目前的對話情境】: {context_str}\n"
        "--------------------------------------------------\n"
        "請遵循以下邏輯提取參數：\n"
        "1. **location**: 優先使用新地點；若無且有情境，則沿用舊地點；否則預設 '國立成功大學'。\n"
        "2. **keyword**: \n"
        "   - 優先使用新需求。\n"
        "   - 若說 '推薦更多'、'還有嗎' -> 沿用舊關鍵字。\n"
        "   - 若描述情境 (如 '天氣冷') -> 推論關鍵字 (如 '火鍋')。\n"
        "   - 預設 '美食'。\n"
        "3. **time_limit**: 預設 20。\n\n"
        f"使用者輸入: {user_text}\n"
        "請只回傳 JSON 字串。"
    )
    
    try:
        response = await llm_generate(system_prompt)
        response = response.replace("```json", "").replace("```", "").strip()
        data = json.loads(response)
        return data
    except:
        if current_context: return current_context
        return {"location": "台南", "keyword": "美食"}

async def run_food_chain(params: dict, original_text: str, exclude_ids: list = []) -> tuple:
    location = params.get("location", "國立成功大學")
    keyword = params.get("keyword", "美食")
    time_limit = params.get("time_limit", 20)
    
    tool_output, new_ids, restaurants_data = await asyncio.to_thread(
        food.find_food, keyword, location, max_travel_time=time_limit, exclude_ids=exclude_ids
    )

    if "Error" in tool_output or "Found places, but none" in tool_output or "已經推薦過囉" in tool_output:
        return tool_output, [], []

    final_prompt = (
        "你是一個專業的台南美食嚮導。請根據以下的餐廳數據回覆使用者。\n"
        "----------------\n"
        f"【使用者原始需求】: \"{original_text}\"\n"
        f"【搜尋關鍵字】: {keyword}\n"
        "----------------\n"
        "以下是搜尋結果數據：\n"
        f"{tool_output}\n" 
    )

    llm_response = await llm_generate(final_prompt)
    return llm_response, new_ids, restaurants_data

# ====== Discord Client ======
class MyClient(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()

dc = MyClient()

# ====== 指令 1: /eat ======
@dc.tree.command(name="eat", description="推薦美食 (支援情境與更多推薦)")
@app_commands.describe(需求="想吃什麼？(例: 牛肉湯 成大 / 還有嗎 / 天氣冷想吃鍋)")
async def eat(interaction: discord.Interaction, 需求: str):
    await interaction.response.defer(thinking=True)
    
    user_id = interaction.user.id
    last_context = user_contexts.get(user_id)
    
    analysis = await analyze_request(需求, current_context=last_context)
    
    current_exclude_ids = []
    if last_context:
        if (analysis.get("location") == last_context.get("location") and 
            analysis.get("keyword") == last_context.get("keyword")):
            current_exclude_ids = last_context.get("seen_ids", [])
    
    ans, new_ids, restaurants_data = await run_food_chain(analysis, original_text=需求, exclude_ids=current_exclude_ids)
    
    if new_ids:
        analysis["seen_ids"] = current_exclude_ids + new_ids
        user_contexts[user_id] = analysis
    
    view = None
    if restaurants_data:
        view = RestaurantView(restaurants_data)
        
    if len(ans) > 1900:
        chunks = [ans[i:i+1900] for i in range(0, len(ans), 1900)]
        for i, chunk in enumerate(chunks):
            if i == len(chunks) - 1:
                await interaction.followup.send(chunk, view=view)
            else:
                await interaction.followup.send(chunk)
    else:
        await interaction.followup.send(ans, view=view)

# ====== 指令 2: /list (查看清單) ======
@dc.tree.command(name="list", description="查看我的待吃清單")
async def list_saved(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    saved = user_saved_lists.get(user_id, [])
    
    if not saved:
        # 這裡改為公開顯示也無妨，或設為 ephemeral=True 較隱私
        # 使用者要求「所有人都能看到」，所以 ephemeral=False
        await interaction.response.send_message(f"📋 **{interaction.user.name} 的待吃清單** 目前是空的！", ephemeral=False)
    else:
        ans = f"📋 **{interaction.user.name} 的待吃清單：**\n\n"
        for idx, item in enumerate(saved, 1):
            ans += f"{idx}. **{item['name']}** ({item['rating']})\n   🔗 {item['map_link']}\n"
        
        # [修改] 使用者要求公開顯示，所以 ephemeral=False
        await interaction.response.send_message(ans, ephemeral=False)

# ====== 指令 3: /delete (刪除項目) ======
@dc.tree.command(name="delete", description="從待吃清單中刪除餐廳")
@app_commands.describe(店名="請輸入要刪除的餐廳名稱(或部分名稱)")
async def delete_saved(interaction: discord.Interaction, 店名: str):
    user_id = str(interaction.user.id)
    saved = user_saved_lists.get(user_id, [])
    
    if not saved:
        await interaction.response.send_message("❌ 你的清單是空的，沒東西可刪。", ephemeral=True)
        return

    # 搜尋要刪除的店 (模糊比對)
    to_remove = None
    for item in saved:
        if 店名 in item['name']:
            to_remove = item
            break
            
    if to_remove:
        saved.remove(to_remove)
        user_saved_lists[user_id] = saved
        save_data() # 立即存檔
        await interaction.response.send_message(f"🗑️ 已將 **{to_remove['name']}** 從清單中移除。", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ 找不到包含「{店名}」的餐廳。請檢查名稱是否正確。", ephemeral=True)

if __name__ == "__main__":
    dc.run(DISCORD_TOKEN)