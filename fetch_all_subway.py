#!/usr/bin/env python3
"""抓取高德地图全部城市的地铁绘制数据，按城市分文件夹保存到 data/。

数据来源：map.amap.com/service/subway?srhdata=...
每个城市保存两份：
  {adcode}_drw_{spell}.json   绘制数据（站点坐标 + 线路路径点）
  {adcode}_info_{spell}.json  站点信息数据（可选，失败时跳过）
"""
import json
import os
import sys
import time
import urllib.request

BASE = "https://map.amap.com/service/subway"
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
CITYLIST_URL = f"{BASE}?srhdata=citylist.json"


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def main():
    os.makedirs(ROOT, exist_ok=True)

    print("获取城市列表...")
    citylist = json.loads(fetch(CITYLIST_URL))["citylist"]
    print(f"共 {len(citylist)} 个城市\n")

    ok = 0
    fail = 0

    for idx, city in enumerate(citylist, 1):
        code = city["adcode"]
        spell = city["spell"]
        name = city["cityname"]

        city_dir = os.path.join(ROOT, f"{code}_{spell}")
        os.makedirs(city_dir, exist_ok=True)

        drw_name = f"{code}_drw_{spell}.json"
        info_name = f"{code}_info_{spell}.json"
        drw_path = os.path.join(city_dir, drw_name)
        info_path = os.path.join(city_dir, info_name)

        # drw：必需
        try:
            text = fetch(f"{BASE}?srhdata={drw_name}")
            json.loads(text)  # 校验合法性
            with open(drw_path, "w", encoding="utf-8") as f:
                f.write(text)
            drw_ok = True
        except Exception as e:
            drw_ok = False
            print(f"[{idx}/{len(citylist)}] {name}({code}) drw 失败: {e}")

        # info：可选
        try:
            text = fetch(f"{BASE}?srhdata={info_name}")
            json.loads(text)
            with open(info_path, "w", encoding="utf-8") as f:
                f.write(text)
            info_ok = True
        except Exception:
            info_ok = False

        if drw_ok:
            ok += 1
            status = "info" if info_ok else "仅drw"
        else:
            fail += 1
            status = "失败"
        print(f"[{idx}/{len(citylist)}] {name}({code}/{spell}) -> {status}")
        time.sleep(0.2)

    # 保存城市列表
    with open(os.path.join(ROOT, "citylist.json"), "w", encoding="utf-8") as f:
        json.dump({"citylist": citylist}, f, ensure_ascii=False, indent=2)

    print(f"\n完成：成功 {ok}，失败 {fail}")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
