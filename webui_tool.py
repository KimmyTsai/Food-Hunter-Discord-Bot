"""
title: Food Hunter Ultimate (Dish Recommender)
author: Kimmy
author_url: https://github.com/open-webui
funding_url: https://github.com/open-webui
version: 0.6.0
"""

import os
import requests
import random
import json
import math

# ==========================================
# 設定區：請在此填入你的 Google Places API Key
# ==========================================
GOOGLE_API_KEY = "MY_API_KEY"
# ==========================================


class Tools:
    def __init__(self):
        self.citation = True

    def get_coordinates(self, location_name: str):
        if not location_name:
            return None, None
        url = "https://maps.googleapis.com/maps/api/geocode/json"
        params = {"address": location_name, "key": GOOGLE_API_KEY, "language": "zh-TW"}
        try:
            res = requests.get(url, params=params).json()
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
            "key": GOOGLE_API_KEY,
        }
        try:
            res = requests.get(url, params=params).json()
            if res["status"] != "OK":
                return {}
            travel_data = {}
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
        """
        取得詳細資料：營業時間、官方摘要、用戶評論
        """
        url = "https://maps.googleapis.com/maps/api/place/details/json"
        # fields 增加: reviews, editorial_summary
        params = {
            "place_id": place_id,
            "fields": "opening_hours,reviews,editorial_summary",
            "language": "zh-TW",
            "key": GOOGLE_API_KEY,
        }

        info = {
            "opening_hours": [],
            "reviews_summary": "無評論資料",
            "editorial_summary": "",
        }

        try:
            res = requests.get(url, params=params).json()
            if res.get("status") == "OK":
                result = res.get("result", {})

                # 1. 營業時間
                info["opening_hours"] = result.get("opening_hours", {}).get(
                    "weekday_text", []
                )

                # 2. 官方摘要 (例如: "知名老店，主打牛肉湯")
                summary = result.get("editorial_summary", {}).get("overview", "")
                info["editorial_summary"] = summary

                # 3. 處理評論 (擷取前 5 則評論的文字)
                reviews = result.get("reviews", [])
                if reviews:
                    # 將評論串接起來，但限制長度以免 Prompt 太長
                    combined_reviews = " | ".join([r.get("text", "") for r in reviews])
                    # 簡單截斷，避免超過 Token 上限
                    info["reviews_summary"] = combined_reviews[:1000]

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
    ) -> str:

        if not GOOGLE_API_KEY or GOOGLE_API_KEY == "YOUR_GOOGLE_API_KEY":
            return "Error: API Key missing."

        origin_lat, origin_lng = self.get_coordinates(location)
        if not origin_lat:
            return f"Error: Cannot find location '{location}'."

        url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
        params = {
            "query": keyword,
            "location": f"{origin_lat},{origin_lng}",
            "radius": 5000,
            "language": "zh-TW",
            "opennow": "true",
            "type": "restaurant",
            "key": GOOGLE_API_KEY,
        }

        try:
            res = requests.get(url, params=params).json()
            if res.get("status") != "OK":
                return "No results found."

            candidates = [
                p
                for p in res.get("results", [])
                if p.get("rating", 0) >= min_rating
                and p.get("user_ratings_total", 0) >= min_reviews
            ]

            if not candidates:
                return "No places match the criteria."

            top_candidates = candidates[:10]
            candidate_ids = [p["place_id"] for p in top_candidates]
            travel_info = self.calculate_travel_times(
                origin_lat, origin_lng, candidate_ids
            )

            final_list = []
            for p in top_candidates:
                pid = p["place_id"]
                if (
                    pid in travel_info
                    and travel_info[pid]["minutes"] <= max_travel_time
                ):
                    p["travel_time_text"] = travel_info[pid]["text"]
                    final_list.append(p)

            if not final_list:
                return f"Found places, but none are within {max_travel_time} mins."

            selected = random.sample(final_list, min(3, len(final_list)))

            output_data = []
            for p in selected:
                # 呼叫更新後的 details 函式
                details = self.get_place_details(p["place_id"])

                output_data.append(
                    {
                        "name": p.get("name"),
                        "rating": f"{p.get('rating')} ({p.get('user_ratings_total')} reviews)",
                        "travel_time": p.get("travel_time_text"),
                        "address": p.get("formatted_address"),
                        "map_link": f"https://www.google.com/maps/place/?q=place_id:{p.get('place_id')}",
                        # 這些資料是給 LLM 讀的原料
                        "details_data": details,
                    }
                )

            return f"""
The user is looking for "{keyword}" within {max_travel_time} mins from "{location}".
Here are the selected restaurants with reviews data:

{json.dumps(output_data, ensure_ascii=False, indent=2)}

Instruction for AI:
1. List the restaurants with Name, Rating, Travel Time, and Map Link.
2. **Display Opening Hours**: Summarize the opening hours (e.g., "Today: 11:00-21:00").
3. **Analyze & Recommend Dishes (IMPORTANT)**: 
   - Read the `reviews_summary` and `editorial_summary` in the data.
   - Extract 2-3 specific dishes that users frequently mention or praise (e.g., "Recommended: Beef Soup, Fried Rice").
   - If no specific dishes are mentioned in the text, infer from the restaurant name or say "Check link for menu".
4. Keep the tone helpful and appetizing.
"""

        except Exception as e:
            return f"Error: {str(e)}"
