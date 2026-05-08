import requests
import json

# 高德地图官方网页版使用的静态地铁数据接口 (3301 是杭州)
url = "http://map.amap.com/service/subway?_1615466846905&srhdata=3301_drw_hangzhou.json"

print("开始连接高德服务器，获取杭州地铁数据...")

# 发送网络请求
response = requests.get(url)
response.encoding = 'utf-8'

if response.status_code == 200:
    subway_data = json.loads(response.text)
    
    # 在当前文件夹保存为 json 文件
    with open("hangzhou_subway.json", "w", encoding="utf-8") as file:
        json.dump(subway_data, file, indent=4, ensure_ascii=False)
        
    print("杭州地铁 JSON 数据已经成功下载！请查看左侧文件列表。")
else:
    print("获取失败，状态码:", response.status_code)