import os
import requests
import random
import json
import math

class Tools:
    """
    Food recommendation tool mimicking webui_tool.py logic.
    Returns:
    1. A constructed prompt with JSON data for the LLM.
    2. A list of place_ids selected (for dedup).
    3. A list of dicts containing restaurant details (for UI buttons).
    """

    GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")

    def __init__(self):
        if not self.GOOGLE_API_KEY:
            print("⚠️ Warning: GOOGLE_API_KEY not set in environment variables.")

    def get_coordinates(self, location_name: str):
        if not location_name:
            return None, None
        url = "https://maps.googleapis.com/maps/api/geocode/json"
        params = {"address": location_name, "key": self.GOOGLE_API_KEY, "language": "zh-TW"}
        try:
            res = requests.get(url, params=params, timeout=10).json()
            if res["status"] == "OK":
                loc = res["results"][0]["geometry"]["location"]
                return loc["lat"], loc["lng"]
            return None, None
        except:
            return None, None

    def calculate_travel_times(self, origin_lat, origin_lng, destinations):
        if not destinations:
            return {}
        url = "https://maps.googleapis.com/maps/api/distancematrix/json"
        dest_str = "|".join([f"place_id:{pid}" for pid in destinations])
        params = {
            "origins": f"{origin_lat},{origin_lng}",
            "destinations": dest_str,
            "mode": "driving",
            "language": "zh-TW",
            "key": self.GOOGLE_API_KEY,
        }
        try:
            res = requests.get(url, params=params, timeout=10).json()
            if res["status"] != "OK":
                return {}
            travel_data = {}
            if "rows" in res and res["rows"]:
                elements = res["rows"][0]["elements"]
                for i, element in enumerate(elements):
                    if element["status"] == "OK":
                        travel_data[destinations[i]] = {
                            "text": element["duration"]["text"],
                            "minutes": math.ceil(element["duration"]["value"] / 60),
                        }
            return travel_data
        except:
            return {}

    def get_place_details(self, place_id):
        url = "https://maps.googleapis.com/maps/api/place/details/json"
        params = {
            "place_id": place_id,
            "fields": "opening_hours,reviews,editorial_summary",
            "language": "zh-TW",
            "key": self.GOOGLE_API_KEY,
        }

        info = {
            "opening_hours": [],
            "reviews_summary": "無評論資料",
            "editorial_summary": "",
        }

        try:
            res = requests.get(url, params=params, timeout=10).json()
            if res.get("status") == "OK":
                result = res.get("result", {})
                info["opening_hours"] = result.get("opening_hours", {}).get("weekday_text", [])
                summary = result.get("editorial_summary", {}).get("overview", "")
                info["editorial_summary"] = summary
                reviews = result.get("reviews", [])
                if reviews:
                    combined_reviews = " | ".join([r.get("text", "") for r in reviews])
                    info["reviews_summary"] = combined_reviews[:1500] 
            return info
        except:
            return info

    def find_food(
        self,
        keyword: str,
        location: str = "國立成功大學",
        max_travel_time: int = 20,
        min_rating: float = 3.5,
        min_reviews: int = 0,
        exclude_ids: list = None 
    ) -> tuple: 
        # 回傳值: (Prompt字串, ID列表, 餐廳詳細資料列表)
        
        if not self.GOOGLE_API_KEY:
            return "Error: API Key missing.", [], []

        if exclude_ids is None:
            exclude_ids = []

        origin_lat, origin_lng = self.get_coordinates(location)
        if not origin_lat:
            return f"Error: Cannot find location '{location}'.", [], []

        url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
        params = {
            "query": keyword,
            "location": f"{origin_lat},{origin_lng}",
            "radius": 5000,
            "language": "zh-TW",
            "opennow": "true",
            "type": "restaurant",
            "key": self.GOOGLE_API_KEY,
        }

        try:
            res = requests.get(url, params=params, timeout=10).json()
            if res.get("status") != "OK":
                return "No results found.", [], []

            candidates = [
                p for p in res.get("results", [])
                if p.get("rating", 0) >= min_rating
                and p.get("user_ratings_total", 0) >= min_reviews
                and p.get("place_id") not in exclude_ids 
            ]

            if not candidates:
                if res.get("results") and len(exclude_ids) > 0:
                    return f"附近的「{keyword}」都已經推薦過囉！試試看換個關鍵字或地點吧。", [], []
                return f"No places found matching criteria.", [], []

            # 取前 15 筆計算時間
            top_candidates = candidates[:15]
            candidate_ids = [p["place_id"] for p in top_candidates]
            travel_info = self.calculate_travel_times(origin_lat, origin_lng, candidate_ids)

            final_list = []
            for p in top_candidates:
                pid = p["place_id"]
                if pid in travel_info and travel_info[pid]["minutes"] <= max_travel_time:
                    p["travel_time_text"] = travel_info[pid]["text"]
                    final_list.append(p)

            if not final_list:
                return f"Found places, but none are within {max_travel_time} mins.", [], []

            selected = random.sample(final_list, min(3, len(final_list)))
            
            selected_ids = [p["place_id"] for p in selected]

            output_data = []
            for p in selected:
                details = self.get_place_details(p["place_id"])
                output_data.append({
                    "name": p.get("name"),
                    "rating": f"{p.get('rating')} ({p.get('user_ratings_total')} reviews)",
                    "travel_time": p.get("travel_time_text"),
                    "address": p.get("formatted_address"),
                    "map_link": f"https://www.google.com/maps/place/?q=place_id:{p.get('place_id')}",
                    "details_data": details,
                })

            prompt = f"""
The user is looking for "{keyword}" within {max_travel_time} mins from "{location}".
Here are the selected restaurants with reviews data:

{json.dumps(output_data, ensure_ascii=False, indent=2)}

Instruction for AI (Reply in Traditional Chinese):
1. **Contextual Response**: If the user mentioned a specific situation (e.g., "It's cold"), start or end by explaining why these restaurants fit that situation.
2. **List the restaurants** using this EXACT format:

[Emoji Number] **[Restaurant Name]**
評分：[Rating]
車程：[Travel Time]
地圖：[Link]
營業時間：[Summarize opening hours]
推薦菜品
  • [Dish 1] – [Brief description based on reviews]
  • [Dish 2] – [Description based on reviews]

3. **Analyze & Recommend Dishes**: Extract specific dishes from `reviews_summary`.
4. Keep the tone friendly and helpful.
"""
            return prompt, selected_ids, output_data

        except Exception as e:
            return f"Error: {str(e)}", [], []