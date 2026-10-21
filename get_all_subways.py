# -*- coding: utf-8 -*-
"""
批量下载高德地铁官网的全部城市数据。

数据来源（与 get_subway.py 完全一致）：
    http://map.amap.com/service/subway?_<时间戳>&srhdata=<城市编号>_drw_<城市拼音>.json

保存方式：
    data/<城市拼音>/<城市拼音>_subway.json

    - 文件名沿用原来的命名方式：{城市拼音}_subway.json
    - JSON 内容保持高德原始结构，不做任何改写（含 l / st / f / cl / ln 等字段，
      后端算中点用 st.n + st.sl，前端画图用 f + st.p + cl + ln）

用法：
    python3 get_all_subways.py            # 跳过已下载成功的城市
    python3 get_all_subways.py --force    # 强制重新下载所有城市
"""
import json
import os
import sys
import time

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CITIES_FILE = os.path.join(BASE_DIR, "cities.json")

# 与高德官网同款的缓存参数，避免命中缓存
URL_TEMPLATE = "http://map.amap.com/service/subway?_{ts}&srhdata={code}_drw_{name}.json"


def load_cities():
    with open(CITIES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def download_city(city, force=False):
    code = city["code"]
    name = city["name"]
    label = city["label"]

    city_dir = os.path.join(DATA_DIR, name)
    out_path = os.path.join(city_dir, f"{name}_subway.json")

    if not force and os.path.exists(out_path):
        try:
            with open(out_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("l"):
                print(f"[跳过] {label} ({name}) 已存在，共 {len(data['l'])} 条线路")
                return True
        except Exception:
            pass  # 文件损坏则重新下载

    url = URL_TEMPLATE.format(ts=int(time.time() * 1000), code=code, name=name)
    try:
        resp = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        resp.encoding = "utf-8"
        if resp.status_code != 200:
            print(f"[失败] {label} ({name}) 状态码 {resp.status_code}")
            return False

        data = json.loads(resp.text)
        if not data.get("l"):
            print(f"[警告] {label} ({name}) 返回内容为空，可能暂无地铁数据")
            return False

        os.makedirs(city_dir, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

        print(f"[成功] {label} ({name}) 共 {len(data['l'])} 条线路 -> data/{name}/{name}_subway.json")
        return True
    except Exception as e:
        print(f"[异常] {label} ({name}) {e!r}")
        return False


def main():
    force = "--force" in sys.argv
    cities = load_cities()
    print(f"共 {len(cities)} 个城市，开始下载...")
    print("-" * 60)

    ok = 0
    failed = []
    for idx, city in enumerate(cities, 1):
        print(f"[{idx}/{len(cities)}] ", end="")
        if download_city(city, force=force):
            ok += 1
        else:
            failed.append(city["label"])
        time.sleep(0.2)  # 稍作停顿，避免请求过快

    print("-" * 60)
    print(f"完成：成功 {ok} / {len(cities)}")
    if failed:
        print(f"失败列表：{', '.join(failed)}（可再次运行脚本重试）")


if __name__ == "__main__":
    main()
