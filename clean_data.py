import json

print("开始清洗高德原始地铁数据...")

# 1. 打开我们刚才下载的原始文件 (模式为 "r" 代表 read 读取)
with open("hangzhou_subway.json", "r", encoding="utf-8") as file:
    raw_data = json.load(file)

# 准备一个空的“干净盒子”（字典），用来装我们提取出来的数据
clean_stations = {}

# 2. 就像剥洋葱一样，一层一层提取数据
# 高德的结构是：先有一堆线路("l")，线路里有一堆站点("st")
# 站点的名字叫 "n"，经纬度坐标叫 "sl"
for line in raw_data.get("l", []):            # 遍历每一条线路
    for station in line.get("st", []):        # 遍历线路上的每一个站点
        name = station.get("n")               # 获取站名
        coords = station.get("sl")            # 获取经纬度
        
        # 把提取到的站名和坐标，放进我们的“干净盒子”里
        if name and coords:
            clean_stations[name] = coords

# 3. 把装满干净数据的盒子，存成一个新的 JSON 文件 (模式为 "w" 代表 write 写入)
with open("clean_stations.json", "w", encoding="utf-8") as file:
    json.dump(clean_stations, file, indent=4, ensure_ascii=False)

# 打印一下我们一共提取了多少个车站
print(f"清洗完成！成功提取了 {len(clean_stations)} 个地铁站的坐标。")
print("请查看左侧新生成的 clean_stations.json 文件！")