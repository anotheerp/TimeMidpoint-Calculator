from fastapi import FastAPI
import networkx as nx
import json
import os
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
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

EDGE_WEIGHT = 3  # 相邻两站之间默认按 3 分钟计算


def load_cities():
    """读取 cities.json 城市清单，并按拼音名建立索引。"""
    global CITIES
    with open(os.path.join(BASE_DIR, "cities.json"), "r", encoding="utf-8") as f:
        CITIES = json.load(f)


def build_city_graph(name):
    """为单个城市构建地铁图。数据沿用高德原始结构：l -> st -> n / sl。"""
    path = os.path.join(DATA_DIR, name, f"{name}_subway.json")
    g = nx.Graph()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for line in data.get("l", []):
            stations = line.get("st", [])
            for i in range(len(stations)):
                current = stations[i].get("n")
                g.add_node(current, coords=stations[i].get("sl"))
                if i < len(stations) - 1:
                    nxt = stations[i + 1].get("n")
                    g.add_edge(current, nxt, weight=EDGE_WEIGHT)
    except Exception as e:
        print(f"[警告] 构建 {name} 地铁图失败：{e}")
    return g


def build_all_graphs():
    print("正在构建全部城市的地铁网络...")
    for city in CITIES:
        GRAPHS[city["name"]] = build_city_graph(city["name"])
        node_count = GRAPHS[city["name"]].number_of_nodes()
        print(f"  {city['label']} ({city['name']})：{node_count} 个站点")
    print("构建完成。")


load_cities()
build_all_graphs()


@app.get("/", response_class=HTMLResponse)
def serve_webpage():
    try:
        with open(os.path.join(BASE_DIR, "index.html"), "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "<h1>找不到 index.html，请确认文件与 main.py 在同一目录下！</h1>"


@app.get("/api/cities")
def list_cities():
    """返回城市清单，供前端下拉框使用。"""
    return {"status": "success", "data": CITIES}


@app.get("/api/midpoint")
def calculate_midpoint(city: str, stations: str):
    """给定 2~5 个地铁站，找出到各站耗时最均衡的「中点」。"""
    G = GRAPHS.get(city)
    if G is None:
        return {"error": "未知城市，请先确认该城市数据已下载"}

    targets = [s.strip() for s in stations.split(",") if s.strip()]
    if len(targets) < 2:
        return {"error": "至少需要选择两个地铁站"}
    if len(targets) > 5:
        return {"error": "最多选择五个地铁站"}

    missing = [s for s in targets if s not in G]
    if missing:
        return {"error": "找不到站点：" + "、".join(missing)}

    # 对每个目标站做单源最短路，得到「候选站到该目标站的时间」
    times_from = [nx.single_source_dijkstra_path_length(G, t, weight="weight") for t in targets]

    results = []
    for station in G.nodes:
        ts = [t[station] for t in times_from if station in t]
        if len(ts) != len(targets):
            continue  # 不可达的候选站跳过
        diff = max(ts) - min(ts)
        results.append({
            "station": station,
            "times": ts,               # 到各选中站的时间
            "diff": diff,              # 时间极差（越小越均衡）
            "total_time": sum(ts),
            "coords": G.nodes[station].get("coords"),
        })

    # 优先时间最均衡，其次总耗时最短
    results.sort(key=lambda x: (x["diff"], x["total_time"]))
    return {"status": "success", "data": results[:10]}
