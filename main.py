from fastapi import FastAPI
import networkx as nx
import json
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse # 新增：用于返回 HTML 网页
from fastapi.staticfiles import StaticFiles # 新增：用于提供静态文件(比如JSON)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 新增：让前端能够直接读取当前目录下的 JSON 文件
app.mount("/data", StaticFiles(directory="."), name="data")

G = nx.Graph()

def build_subway_graph():
    print("正在构建地铁网络...")
    try:
        with open("hangzhou_subway.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        for line in data.get("l", []):
            stations = line.get("st", [])
            for i in range(len(stations)):
                current_station = stations[i].get("n")
                G.add_node(current_station, coords=stations[i].get("sl"))
                if i < len(stations) - 1:
                    next_station = stations[i+1].get("n")
                    G.add_edge(current_station, next_station, weight=3)
    except Exception as e:
        print("错误:", e)

build_subway_graph()

# 新增：当你访问主页 http://127.0.0.1:8000/ 时，直接显示 index.html
@app.get("/", response_class=HTMLResponse)
def serve_webpage():
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "<h1>找不到 index.html，请确认文件在同一目录下建立好了！</h1>"

# 之前的算法接口保持不变
@app.get("/api/midpoint")
def calculate_midpoint(station_a: str, station_b: str, tolerance: int = 5):
    if station_a not in G or station_b not in G:
        return {"error": "找不到指定的地铁站"}

    times_from_a = nx.single_source_dijkstra_path_length(G, station_a, weight='weight')
    times_from_b = nx.single_source_dijkstra_path_length(G, station_b, weight='weight')

    results = []
    for station in G.nodes:
        if station in times_from_a and station in times_from_b:
            time_a = times_from_a[station]
            time_b = times_from_b[station]
            time_diff = abs(time_a - time_b)
            if time_diff <= tolerance:
                results.append({
                    "station": station,
                    "time_a": time_a,
                    "time_b": time_b,
                    "time_diff": time_diff,
                    "total_time": time_a + time_b,
                    "coords": G.nodes[station].get("coords")
                })
    return {"status": "success", "data": sorted(results, key=lambda x: x["total_time"])[:10]}