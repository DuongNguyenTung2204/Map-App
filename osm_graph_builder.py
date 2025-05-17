import osmium
import networkx as nx
import math
import json
import logging
import re

# Thiết lập logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Hàm tính khoảng cách Haversine (km)
def haversine(lon1, lat1, lon2, lat2):
    R = 6371  # Bán kính Trái Đất (km)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.asin(math.sqrt(a))
    return R * c

# Hàm lấy tốc độ từ tags
def get_speed(tags):
    # Tốc độ mặc định theo loại đường (km/h)
    default_speeds = {
        'motorway': 80, 'motorway_link': 80,
        'trunk': 60, 'trunk_link': 60,
        'primary': 50, 'primary_link': 50,
        'secondary': 40, 'secondary_link': 40,
        'tertiary': 30, 'tertiary_link': 30,
        'unclassified': 25, 'residential': 25,
        'service': 20,
        'track': 15
    }

    # Lấy maxspeed từ tags
    maxspeed = tags.get('maxspeed')
    if maxspeed:
        # Xử lý các định dạng maxspeed (ví dụ: "50", "50 km/h", "30 mph")
        match = re.match(r'^(\d+)\s*(km/h|mph)?$', maxspeed)
        if match:
            speed = int(match.group(1))
            unit = match.group(2)
            if unit == 'mph':
                speed = speed * 1.60934  # Chuyển mph sang km/h
            return speed

    # Nếu không có maxspeed hợp lệ, dùng tốc độ mặc định
    highway_type = tags.get('highway', 'unclassified')
    return default_speeds.get(highway_type, 25)  # Mặc định 25 km/h nếu không xác định

# Class để xử lý file OSM
class RoadGraphHandler(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.nodes = {}  # Lưu tọa độ node: {node_id: (lat, lon)}
        self.edges = []  # Lưu cạnh: [(node1, node2, weight, tags)]
        # Danh sách các loại highway dành cho xe
        self.vehicle_highways = [
            'motorway', 'trunk', 'primary', 'secondary', 'tertiary',
            'unclassified', 'residential', 'service', 'track',
            'motorway_link', 'trunk_link', 'primary_link', 'secondary_link', 'tertiary_link'
        ]

    def node(self, n):
        self.nodes[n.id] = (n.location.lat, n.location.lon)

    def way(self, w):
        if 'highway' not in w.tags:
            return  # Bỏ qua nếu không phải đường bộ

        # Chỉ xử lý các loại highway dành cho xe
        highway_type = w.tags['highway']
        if highway_type not in self.vehicle_highways:
            logging.info(f"Bỏ qua way {w.id} vì không phải đường xe: highway={highway_type}")
            return

        # Kiểm tra đường một chiều
        is_oneway = w.tags.get('oneway', 'no') == 'yes'

        # Lấy danh sách node của way
        node_refs = [n.ref for n in w.nodes]
        tags = {t.k: t.v for t in w.tags}
        tags['id'] = str(w.id)  # Thêm way_id vào tags

        # Tạo cạnh giữa các node liên tiếp
        for i in range(len(node_refs) - 1):
            n1, n2 = node_refs[i], node_refs[i + 1]
            if n1 in self.nodes and n2 in self.nodes:
                lat1, lon1 = self.nodes[n1]
                lat2, lon2 = self.nodes[n2]
                # Tính chiều dài đoạn đường (km)
                length = haversine(lon1, lat1, lon2, lat2)
                # Lấy tốc độ (km/h)
                speed = get_speed(tags)
                # Tính trọng số = thời gian di chuyển (giờ)
                weight = length / speed
                self.edges.append((n1, n2, weight, tags))
                if not is_oneway:
                    self.edges.append((n2, n1, weight, tags))  # Thêm cạnh ngược lại
                logging.info(f"Cạnh ({n1}, {n2}), way_id={tags['id']}, length={length:.3f} km, "
                             f"speed={speed} km/h, weight={weight:.6f} giờ")

def build_graph(osm_file, graphml_file='road_network.graphml'):
    logging.info(f"Xây dựng đồ thị từ file OSM: {osm_file}")
    handler = RoadGraphHandler()
    handler.apply_file(osm_file)

    # Tạo đồ thị có hướng
    G = nx.DiGraph()
    for node_id, (lat, lon) in handler.nodes.items():
        G.add_node(node_id, lat=lat, lon=lon)
    for n1, n2, weight, tags in handler.edges:
        tags_str = json.dumps(tags)
        G.add_edge(n1, n2, weight=weight, tags=tags_str)

    logging.info(f"Đã tạo đồ thị với {G.number_of_nodes()} node và {G.number_of_edges()} cạnh")
    nx.write_graphml(G, graphml_file)
    logging.info(f"Đã lưu đồ thị vào {graphml_file}")
    return G

if __name__ == "__main__":
    osm_file = 'kim_lien.osm'
    build_graph(osm_file)