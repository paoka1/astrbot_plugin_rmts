"""
高德天气查询，移植自 rmts/plugins/chat/functions/info/weather.py（只保留天气预报）
"""

import httpx

BASE_URL = "https://restapi.amap.com/v3/weather/weatherInfo"
WEEK_NAMES = {
    "1": "周一",
    "2": "周二",
    "3": "周三",
    "4": "周四",
    "5": "周五",
    "6": "周六",
    "7": "周日",
}


class WeatherError(Exception):
    """天气查询异常"""


async def get_forecast_text(api_key: str, city: str, timeout: float = 10.0) -> str:
    """查询未来几天的天气预报，返回易读文本"""
    if not city:
        raise ValueError("城市参数不能为空")

    params = {"key": api_key, "city": city, "output": "json", "extensions": "all"}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(BASE_URL, params=params)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPError as e:
        raise WeatherError(f"网络请求失败: {e}")
    except ValueError as e:
        raise WeatherError(f"JSON 解析失败: {e}")

    if data.get("status") != "1":
        raise WeatherError(
            f"API 返回错误: {data.get('info', '未知错误')} (code: {data.get('infocode', '')})"
        )

    forecasts = data.get("forecasts") or []
    if not forecasts or not forecasts[0].get("casts"):
        return "暂无天气预报数据"

    forecast = forecasts[0]
    items = []
    for i, cast in enumerate(forecast["casts"]):
        day_label = (
            "今天"
            if i == 0
            else "明天"
            if i == 1
            else WEEK_NAMES.get(cast.get("week", ""), cast.get("week", ""))
        )

        # 白天和夜间天气相同时合并描述
        if cast.get("dayweather") == cast.get("nightweather"):
            weather_desc = cast.get("dayweather", "")
        else:
            weather_desc = (
                f"白天{cast.get('dayweather', '')}，夜间{cast.get('nightweather', '')}"
            )

        item = f"{day_label}{weather_desc}，{cast.get('nighttemp', '')}~{cast.get('daytemp', '')}摄氏度"
        # 白天和夜间风力相同时才写风力，避免信息过载
        if cast.get("daywind") == cast.get("nightwind") and cast.get(
            "daypower"
        ) == cast.get("nightpower"):
            item += f"，{cast.get('daywind', '')}风{cast.get('daypower', '')}级"
        items.append(item)

    return (
        f"{forecast.get('city', city)}未来{len(items)}天天气预报："
        + "；".join(items)
        + "。"
    )
