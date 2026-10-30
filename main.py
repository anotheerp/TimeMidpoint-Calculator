from fastapi import FastAPI
import networkx as nx
import json
import os
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

# 让前端能读取 data/ 目录下的城市数据和换乘站图标
app.mount("/data", StaticFiles(directory=DATA_DIR), name="data")

# 每个城市一张图：GRAPHS[城市拼音] = nx.Graph
GRAPHS = {}
CITIES = []   # [{code, name, label}, ...]
# 站名 -> 该站在图中的节点列表（换乘站会有多个「站名+线路」节点）
STATION_NODES = {}

EDGE_WEIGHT = 3  # 相邻两站之间默认按 3 分钟计算（无真实数据时的兜底）
FUZZY_TOLERANCE = 5  # 分钟：均衡模式下允许与「绝对均衡」的时间差上限，用于纳入模糊中点
ABSOLUTE_TOLERANCE = 3.0  # 分钟：绝对中点模式下允许的时间差（约一站的误差）
DEFAULT_TRANSFER = 4.0  # 分钟：拿到真实数据后，缺失换乘记录的站点默认换乘耗时

# 首页与城市清单不允许浏览器缓存，保证刷新后拿到的是最新代码
NO_CACHE = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


def load_cities():
    """读取 cities.json 城市清单，并按拼音名建立索引。"""
    global CITIES
    with open(os.path.join(BASE_DIR, "cities.json"), "r", encoding="utf-8") as f:
        CITIES = json.load(f)


def load_travel_times(name):
    """读取 fetch_travel_times.py 生成的真实耗时文件，读不到返回 None。"""
    path = os.path.join(DATA_DIR, name, "travel_times.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[警告] {name} 的 travel_times.json 读取失败：{e}")
        return None


def build_city_graph(name):
    """为单个城市构建地铁图。

    节点是「站名 + 线路」，同一个换乘站的各线路节点之间连一条换乘边，
    这样换乘的步行/候车时间才能计入最短路；没有真实耗时数据时自动退回旧行为
    （每站 3 分钟、换乘 0 分钟）。
    """
    path = os.path.join(DATA_DIR, name, f"{name}_subway.json")
    g = nx.Graph()
    nodes_of = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        tt = load_travel_times(name)
        edges = tt.get("edges", {}) if tt else {}
        transfers = tt.get("transfers", {}) if tt else {}
        used_real = bool(edges)

        for line in data.get("l", []):
            line_name = line.get("ln") or ""
            stations = [s for s in line.get("st", []) if s.get("n")]
            for i in range(len(stations)):
                cur = stations[i].get("n")
                node = (cur, line_name)
                g.add_node(node, coords=stations[i].get("sl"), line=line_name)
                nodes_of.setdefault(cur, []).append(node)
                if i < len(stations) - 1:
                    nxt = stations[i + 1].get("n")
                    key = "|".join(sorted([cur, nxt]))
                    weight = edges.get(key, EDGE_WEIGHT)
                    g.add_edge(node, (nxt, line_name), weight=weight, kind="ride")

        # 换乘边：同一站名的不同线路节点互连
        if tt:
            for station, nodes in nodes_of.items():
                if len(nodes) < 2:
                    continue
                cost = transfers.get(station, DEFAULT_TRANSFER)
                for i in range(len(nodes)):
                    for j in range(i + 1, len(nodes)):
                        if not g.has_edge(nodes[i], nodes[j]):
                            g.add_edge(nodes[i], nodes[j], weight=cost, kind="transfer")

        if used_real:
            print(f"  {name}：已接入真实站间耗时（{len(edges)} 条边、{len(transfers)} 个换乘站）")
    except Exception as e:
        print(f"[警告] 构建 {name} 地铁图失败：{e}")

    STATION_NODES[name] = nodes_of
    return g


def build_all_graphs():
    print("正在构建全部城市的地铁网络...")
    for city in CITIES:
        GRAPHS[city["name"]] = build_city_graph(city["name"])
        station_count = len(STATION_NODES.get(city["name"], {}))
        print(f"  {city['label']} ({city['name']})：{station_count} 个站点")
    print("构建完成。")


load_cities()
build_all_graphs()


@app.get("/", response_class=HTMLResponse)
def serve_webpage():
    try:
        with open(os.path.join(BASE_DIR, "index.html"), "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), headers=NO_CACHE)
    except FileNotFoundError:
        return HTMLResponse(
            content="<h1>找不到 index.html，请确认文件与 main.py 在同一目录下！</h1>",
            status_code=500,
            headers=NO_CACHE,
        )


@app.get("/api/cities")
def list_cities():
    """返回城市清单，供前端下拉框使用。"""
    return JSONResponse({"status": "success", "data": CITIES}, headers=NO_CACHE)


@app.get("/api/midpoint")
def calculate_midpoint(city: str, stations: str, mode: str = "balanced"):
    """给定 2~5 个地铁站，找出到各站耗时最均衡的「中点」。

    mode=balanced（默认）：模糊中点，允许 5 分钟以内的不均衡，优先总耗时最短。
    mode=absolute：绝对中点，只要求各方时间一致（最多 3 分钟误差），不在乎总耗时更长。
    """
    G = GRAPHS.get(city)
    if G is None:
        return {"error": "未知城市，请先确认该城市数据已下载"}

    targets = [s.strip() for s in stations.split(",") if s.strip()]
    if len(targets) < 2:
        return {"error": "至少需要选择两个地铁站"}
    if len(targets) > 5:
        return {"error": "最多选择五个地铁站"}

    nodes_of = STATION_NODES.get(city, {})
    missing = [s for s in targets if s not in nodes_of]
    if missing:
        return {"error": "找不到站点：" + "、".join(missing)}

    # 对每个目标站做多源最短路（该站的所有线路节点都算起点，等于换乘免费进站），
    # 再把节点级距离按站名聚合，得到「候选站到该目标站的时间」
    times_from = []
    for t in targets:
        dist = nx.multi_source_dijkstra_path_length(G, nodes_of[t], weight="weight")
        by_name = {}
        for node, d in dist.items():
            name = node[0]
            if name not in by_name or d < by_name[name]:
                by_name[name] = d
        times_from.append(by_name)

    results = []
    for station in nodes_of:
        if station in targets:
            continue  # 起点自身不作为中点候选
        ts = [t.get(station) for t in times_from]
        if any(v is None for v in ts):
            continue  # 不可达的候选站跳过
        diff = max(ts) - min(ts)
        results.append({
            "station": station,
            "times": [round(v, 1) for v in ts],   # 到各选中站的时间
            "diff": round(diff, 1),               # 时间极差（越小越均衡）
            "total_time": round(sum(ts), 1),
            "coords": G.nodes[nodes_of[station][0]].get("coords"),
        })

    if not results:
        return {"status": "success", "data": []}

    best_diff = min(r["diff"] for r in results)

    if mode == "absolute":
        # 绝对中点：只要求各方耗时一致，最多容忍 3 分钟（一站）的误差，
        # 完全不考虑「绕远」，因此选出的点可能离所有人都很远、总耗时很长
        limit = best_diff + ABSOLUTE_TOLERANCE
        picked = [r for r in results if r["diff"] <= limit]
        picked.sort(key=lambda x: (x["diff"], x["total_time"]))
    else:
        # 模糊中点：不追求绝对均衡，只要时间差不超过下限；
        # 容差内优先平均到达时间最短，其次时间差最小
        limit = max(best_diff, FUZZY_TOLERANCE)
        picked = [r for r in results if r["diff"] <= limit]
        picked.sort(key=lambda x: (x["total_time"], x["diff"]))

    return {"status": "success", "mode": mode, "data": picked[:10]}
