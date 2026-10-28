#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成每个城市「站间真实乘车时间」权重文件：data/<城市拼音>/travel_times.json

两种模式：
  1) estimate —— 不需要任何 key，用站点经纬度算真实距离 + 地铁旅行速度模型估算。
                 用于给全网打底，或在你拿到 key 之前先验证管道。
  2) amap     —— 调用高德开放平台「公交路径规划」接口反推真实耗时。需要 Web 服务类型的 key。
                 注意：高德给的地铁段 duration 里混着约 4~5 分钟的候车/进出站开销，
                 直接查相邻站会得到 7 分钟/站这种荒谬值。因此这里对每条线路查两个
                 长区间，拟合出「每站边际时间 + 固定开销」，再按站间距离分配到每一条边；
                 换乘时间则取两段地铁之间夹的步行时长 + 候车。

用法：
    # 打底（秒出，不联网）
    python3 fetch_travel_times.py --mode estimate

    # 精确抓取（需要 key）
    export AMAP_KEY=你的key
    python3 fetch_travel_times.py --mode amap --cities beijing
    python3 fetch_travel_times.py --mode amap                 # 全部城市
    python3 fetch_travel_times.py --mode amap --only transfers # 只补换乘时间

说明：
  - 结果按城市落盘，已抓过的边会跳过，中断后重跑自动续抓。
  - 高德免费额度有限（常见为每日几千次），全网约 5~8 千次调用，建议按城市分批跑。
"""

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

AMAP_URL = "https://restapi.amap.com/v3/direction/transit/integrated"

# ---- 模型/修正参数（分钟） ----
WAIT_MIN = 2.0          # 换乘时的平均候车时间
MIN_TRANSFER_WALK = 1.0  # 同站换乘的步行下限（高德常给 0，但总要走一段到另一站台）
MIN_RIDE = 1.0          # 一站最短乘车时间（下限保护）
MAX_RIDE = 15.0         # 一站最长乘车时间（上限保护）
DEFAULT_TRANSFER = 4.0  # 拿不到换乘数据时的默认换乘耗时
SPEED_LOCAL = 33.0      # 市区线旅行速度 km/h（含停站）
SPEED_EXPRESS = 45.0    # 郊区/快线旅行速度 km/h
DWELL = 0.6             # 起停附加（分钟）
EXPRESS_GAP = 2.0       # 平均站距大于该值(km)视为快线


def edge_key(a, b):
    """无向边的统一 key。"""
    return "|".join(sorted([a, b]))


def haversine(a, b):
    """两点经纬度距离（km）。"""
    try:
        la1, lo1 = [float(x) for x in a.split(",")]
        la2, lo2 = [float(x) for x in b.split(",")]
    except Exception:
        return None
    r = 6371.0
    p = math.radians
    dlat = p(la2 - la1)
    dlon = p(lo2 - lo1)
    h = math.sin(dlat / 2) ** 2 + math.cos(p(la1)) * math.cos(p(la2)) * math.sin(dlon / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def estimate_ride(dist_km, speed):
    """距离 + 速度模型估算一站的乘车时间（分钟）。"""
    if not dist_km:
        return None
    return round(min(max(DWELL + dist_km / speed * 60, MIN_RIDE), MAX_RIDE), 2)


def line_speed(stations):
    """按线路平均站距判定快线还是市区线。"""
    gaps = []
    for i in range(len(stations) - 1):
        d = haversine(stations[i].get("sl", ""), stations[i + 1].get("sl", ""))
        if d:
            gaps.append(d)
    if not gaps:
        return SPEED_LOCAL
    avg = sum(gaps) / len(gaps)
    return SPEED_EXPRESS if avg > EXPRESS_GAP else SPEED_LOCAL


def collect_lines(city):
    """读原始地铁数据，返回 [{name, stations:[{n, sl}]}]。"""
    path = os.path.join(DATA_DIR, city, f"{city}_subway.json")
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    lines = []
    for line in data.get("l", []):
        st = [{"n": s.get("n"), "sl": s.get("sl")} for s in line.get("st", []) if s.get("n")]
        if len(st) >= 2:
            lines.append({"name": line.get("ln") or "", "stations": st})
    return lines


def collect_transfers(lines):
    """找出出现在 2 条及以上线路上的站（换乘站），返回 {站名: [(线路名, 前站, 后站)]}。"""
    appear = {}
    for line in lines:
        st = line["stations"]
        for i, s in enumerate(st):
            prev_st = st[i - 1]["n"] if i > 0 else None
            next_st = st[i + 1]["n"] if i < len(st) - 1 else None
            appear.setdefault(s["n"], []).append((line["name"], prev_st, next_st))
    return {k: v for k, v in appear.items() if len(v) >= 2}


def load_existing(path):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"meta": {}, "edges": {}, "transfers": {}}


def save(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)


def build_estimate(city, lines, payload):
    """用距离模型补全尚未有数据的边（写入 payload，返回新增数量）。"""
    added = 0
    for line in lines:
        speed = line_speed(line["stations"])
        st = line["stations"]
        for i in range(len(st) - 1):
            k = edge_key(st[i]["n"], st[i + 1]["n"])
            if k in payload["edges"]:
                continue
            t = estimate_ride(haversine(st[i].get("sl", ""), st[i + 1].get("sl", "")), speed)
            if t:
                payload["edges"][k] = t
                added += 1
    for name in collect_transfers(lines):
        payload["transfers"].setdefault(name, DEFAULT_TRANSFER)
    return added


# ------------------------- 高德 API -------------------------

def amap_transit(key, o_lnglat, d_lnglat, city_label, timeout=12, retries=3):
    """调用高德公交路径规划（v3）。返回 (ok, payload_or_error)。

    网络偶发会抛 SSL EOF，重试几次即可，别因此中断整轮抓取。
    """
    if not o_lnglat or not d_lnglat:
        return False, "缺少起终点坐标"
    params = {
        "key": key,
        "origin": o_lnglat,
        "destination": d_lnglat,
        "city": city_label,
        "strategy": "0",
    }
    last = ""
    for i in range(retries):
        try:
            r = requests.get(AMAP_URL, params=params, timeout=timeout)
            return True, r.json()
        except Exception as e:
            last = str(e)
            time.sleep(1.0)
    return False, last


def line_matches(line_name, busline_name):
    """判断返回的地铁 busline 是否就是我们要的那条线路。"""
    if not line_name:
        return True
    core = line_name.replace("地铁", "").strip()
    if core and core in busline_name:
        return True
    return len(core) >= 3 and core[:3] in busline_name


def stop_name(v):
    if isinstance(v, dict):
        return v.get("name") or ""
    return v or ""


def name_match(a, b):
    """站名宽松匹配（高德站名可能与地铁数据略有出入）。"""
    if not a or not b:
        return False
    a, b = a.strip(), b.strip()
    return a == b or a in b or b in a


def metro_durations(resp, line_name=None, from_name=None, to_name=None):
    """取「直达」方案的地铁乘坐秒数，[(秒, 1), ...]，按耗时升序。

    高德会把「坐一小段目标线路再换乘」的方案也列出来，直接取最短会严重低估，
    因此按三档优先级取：
      1) 目标线路 + 上下车站名与查询的起终点一致（最可靠）
      2) 方案里只有一段地铁（即直达，没换乘）
      3) 兜底：仍匹配目标线路的方案
    """
    route = resp.get("route") or {}
    strict, direct, loose = [], [], []
    for tr in route.get("transits") or []:
        metro_all, matched = 0, 0.0
        exact = True
        for sg in tr.get("segments") or []:
            for bl in (sg.get("bus") or {}).get("buslines") or []:
                nm = bl.get("name") or ""
                ty = bl.get("type") or ""
                if "地铁" not in (ty + nm):
                    continue
                metro_all += 1
                if line_name and not line_matches(line_name, nm):
                    continue
                try:
                    matched += float(bl.get("duration") or 0)
                except Exception:
                    pass
                if from_name and to_name:
                    dep = stop_name(bl.get("departure_stop"))
                    arr = stop_name(bl.get("arrival_stop"))
                    if not (name_match(dep, from_name) and name_match(arr, to_name)):
                        exact = False
        if matched <= 0:
            continue
        if from_name and exact:
            strict.append(matched)
        elif metro_all == 1:
            direct.append(matched)
        else:
            loose.append(matched)

    for group in (strict, direct, loose):
        if group:
            return sorted((v, 1) for v in group)
    return []


def transfer_walk_seconds(resp, line_a=None, line_b=None):
    """跨线方案里，两段地铁之间夹着的步行时长（秒）。

    要求方案确实包含指定的两条线路，否则可能取到完全不相关方案的步行时间。
    """
    route = resp.get("route") or {}
    best = None
    for tr in route.get("transits") or []:
        metro_seen, walk = 0, 0.0
        seen_lines = []
        for sg in tr.get("segments") or []:
            is_metro, ln = False, ""
            for bl in (sg.get("bus") or {}).get("buslines") or []:
                nm = bl.get("name") or ""
                ty = bl.get("type") or ""
                if "地铁" in (ty + nm):
                    is_metro, ln = True, nm
            if is_metro:
                metro_seen += 1
                seen_lines.append(ln)
                walk = 0.0          # 只累计两段地铁之间的步行
                continue
            if metro_seen >= 1:
                w = sg.get("walking") or {}
                try:
                    walk += float(w.get("duration") or 0)
                except Exception:
                    pass
        if metro_seen < 2:
            continue
        if line_a and not any(line_matches(line_a, n) for n in seen_lines):
            continue
        if line_b and not any(line_matches(line_b, n) for n in seen_lines):
            continue
        if best is None or walk < best:
            best = walk
    return best


def fit_line_profile(key, city_label, line, delay):
    """对一条线路查两个长区间，拟合「每站边际时间」和「固定开销」。

    高德给的地铁 duration = 候车/进出站开销(固定) + 站数 × 每站运行时间，
    查两个不同长度的区间即可解出这两项，从而剔除候车等噪音。
    """
    st = [s for s in line["stations"] if s.get("sl")]
    n = len(st)
    if n < 3:
        return None
    k1 = max(1, n // 3)
    k2 = n - 1
    if k1 >= k2:
        return None

    samples = []
    for k in (k1, k2):
        ok, resp = amap_transit(key, st[0]["sl"], st[k]["sl"], city_label)
        if not ok:
            print(f"  [网络错误] {resp}")
            return "STOP"
        if str(resp.get("status")) != "1":
            print(f"  [接口] {resp.get('info')} / {resp.get('infocode')}")
            return "STOP"      # 配额用尽 / key 异常，应当停止而不是继续烧额度
        cand = (metro_durations(resp, line["name"], st[0]["n"], st[k]["n"])
                or metro_durations(resp, line["name"]))
        if not cand:
            return None            # 高德没给这条线的地铁方案 → 调用方回退估算
        samples.append((k, cand[0][0]))
        time.sleep(delay)

    (n1, t1), (n2, t2) = samples
    sec_per_stop = (t2 - t1) / (n2 - n1)
    overhead = t1 - sec_per_stop * n1
    # overhead 只作参考，可能为负（高德对部分长区间并不叠加候车时间），
    # 真正使用的是边际时间 sec_per_stop，因此这里只挡明显离谱的值
    if not (30 <= sec_per_stop <= 400) or not (-400 <= overhead <= 900):
        return None
    return {"sec_per_stop": round(sec_per_stop, 1),
            "overhead": round(overhead, 1),
            "stations": n}


def apply_profile(line, prof, edges):
    """把拟合出的「平均每站时间」写成每条边的权重（分钟）。

    高德的地铁时长本质上是「站数 × 平均每站时间」，而不是严格按距离积分；
    若按距离线性分配，长站距区段会被明显高估（实测下沙段会算到 3.8 分钟/站），
    导致算法选出和高德不同的路径。因此这里以平均每站时间为基准，
    只按站距做 ±15% 的微调，兼顾区分长短区间与和高德口径一致。
    """
    st = [s for s in line["stations"] if s.get("sl")]
    n = len(st) - 1
    if n <= 0:
        return
    dists = [haversine(st[i]["sl"], st[i + 1]["sl"]) or 0.0 for i in range(n)]
    avg = sum(dists) / n
    base = prof["sec_per_stop"] / 60.0
    for i in range(n):
        key = edge_key(st[i]["n"], st[i + 1]["n"])
        ratio = 1.0
        if avg > 0:
            ratio = 0.85 + 0.3 * min(dists[i] / avg, 2.0) / 2.0
        edges[key] = round(min(max(base * ratio, MIN_RIDE), MAX_RIDE), 2)


# ------------------------- 主流程 -------------------------

def process_city(city, label, mode, key, only, delay, force):
    lines = collect_lines(city)
    if not lines:
        print(f"  跳过 {city}：没有可用的线路数据")
        return
    path = os.path.join(DATA_DIR, city, "travel_times.json")
    payload = load_existing(path)
    if force:
        payload["edges"], payload["transfers"], payload["lines"] = {}, {}, {}

    station_coord = {}
    for line in lines:
        for s in line["stations"]:
            if s.get("sl"):
                station_coord[s["n"]] = s["sl"]

    todo_edges = []
    for line in lines:
        st = line["stations"]
        for i in range(len(st) - 1):
            k = edge_key(st[i]["n"], st[i + 1]["n"])
            if k not in payload["edges"]:
                todo_edges.append((st[i]["n"], st[i + 1]["n"], k))

    transfers = collect_transfers(lines)
    todo_trans = [n for n in transfers if n not in payload["transfers"]]

    if mode == "estimate":
        added = build_estimate(city, lines, payload)
        payload["meta"] = {
            "city": city, "source": "estimate", "wait_min": WAIT_MIN,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        save(path, payload)
        print(f"  {label}({city})：估算完成，边 {len(payload['edges'])} 条（新增 {added}），换乘站 {len(payload['transfers'])} 个")
        return

    # ---- amap 模式：按线路拟合，再把总运行时间按距离分到每条边 ----
    fetched_l = fetched_t = fallback = 0
    stop = False
    lines_done = payload.setdefault("lines", {})

    if only in ("edges", "both"):
        for idx, line in enumerate(lines):
            name = line["name"] or f"line{idx}"
            if name in lines_done:
                continue
            prof = fit_line_profile(key, label, line, delay)
            if prof == "STOP":
                stop = True
                break
            if prof is None:      # 高德没给这条线的地铁方案 → 距离模型兜底
                build_estimate(city, [line], payload)
                fallback += 1
                continue
            apply_profile(line, prof, payload["edges"])
            lines_done[name] = prof
            fetched_l += 1
            print(f"    {name}：{prof['sec_per_stop']:.0f} 秒/站（含固定开销 {prof['overhead']:.0f} 秒）")
            if fetched_l % 5 == 0:
                save(path, payload)

    if not stop and only in ("transfers", "both"):
        for j, name in enumerate(todo_trans):
            info = transfers[name]
            # 取两条线路各一个相邻站，跨线查询从而逼出换乘步行
            a = info[0][1] or info[0][2]
            b = info[1][2] or info[1][1]
            if (not a or not b or a == b
                    or a not in station_coord or b not in station_coord):
                payload["transfers"][name] = DEFAULT_TRANSFER
                continue
            ok, resp = amap_transit(key, station_coord[a], station_coord[b], label)
            if not ok:
                print(f"  [网络错误] {resp}")
                stop = True
                break
            if str(resp.get("status")) != "1":
                print(f"  [接口拒绝] {resp.get('info')} / {resp.get('infocode')}")
                stop = True
                break
            walk = transfer_walk_seconds(resp, info[0][0], info[1][0])
            if walk is None:
                payload["transfers"][name] = DEFAULT_TRANSFER
            else:
                walk_min = max(walk / 60.0, MIN_TRANSFER_WALK)
                payload["transfers"][name] = round(min(max(walk_min + WAIT_MIN, 2.0), 12.0), 2)
            fetched_t += 1
            if fetched_t % 20 == 0:
                save(path, payload)
                print(f"    换乘进度 {fetched_t}/{len(todo_trans)}")
            time.sleep(delay)

    payload["meta"] = {
        "city": city, "source": "amap", "wait_min": WAIT_MIN,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    save(path, payload)
    print(f"  {label}({city})：拟合线路 {fetched_l} 条（回退估算 {fallback}），换乘 {fetched_t} 个；"
          f"累计边 {len(payload['edges'])}、换乘 {len(payload['transfers'])}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["estimate", "amap"], default="estimate")
    ap.add_argument("--key", default=os.environ.get("AMAP_KEY", ""))
    ap.add_argument("--cities", default="", help="逗号分隔的城市拼音，缺省为全部")
    ap.add_argument("--only", choices=["edges", "transfers", "both"], default="both")
    ap.add_argument("--delay", type=float, default=0.25, help="每次调用间隔秒数，控制 QPS")
    ap.add_argument("--force", action="store_true", help="忽略已有结果重新抓取")
    args = ap.parse_args()

    with open(os.path.join(BASE_DIR, "cities.json"), "r", encoding="utf-8") as f:
        cities = json.load(f)

    if args.cities:
        want = [c.strip() for c in args.cities.split(",") if c.strip()]
        cities = [c for c in cities if c["name"] in want]

    if args.mode == "amap" and not args.key:
        print("缺少 key：请用 --key 传入，或设置环境变量 AMAP_KEY")
        sys.exit(1)

    print(f"模式 {args.mode}，共 {len(cities)} 个城市")
    for c in cities:
        process_city(c["name"], c["label"], args.mode, args.key, args.only, args.delay, args.force)
    print("全部完成。")


if __name__ == "__main__":
    main()
