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

# Hàm tính điểm giao nhau của đoạn thẳng với ranh giới hộp giới hạn
def clip_segment_to_bbox(x1, y1, x2, y2, bbox_min_lat, bbox_min_lon, bbox_max_lat, bbox_max_lon):
    # Dựa trên thuật toán cắt đoạn thẳng Cohen-Sutherland
    def compute_code(x, y):
        code = 0
        if x < bbox_min_lon:
            code |= 1  # Trái
        elif x > bbox_max_lon:
            code |= 2  # Phải
        if y < bbox_min_lat:
            code |= 4  # Dưới
        elif y > bbox_max_lat:
            code |= 8  # Trên
        return code

    code1 = compute_code(x1, y1)
    code2 = compute_code(x2, y2)

    if code1 == 0 and code2 == 0:
        return (x1, y1), (x2, y2)  # Cả hai điểm trong bbox

    if code1 & code2 != 0:
        return None  # Đoạn thẳng hoàn toàn ngoài bbox

    # Cắt đoạn thẳng
    clipped_points = []
    for _ in range(2):  # Thử cắt cả hai đầu
        code = code1 if _ == 0 else code2
        x, y = (x1, y1) if _ == 0 else (x2, y2)
        if code == 0:
            clipped_points.append((x, y))
            continue

        # Tính giao điểm với ranh giới
        if code & 1:  # Trái
            y = y1 + (y2 - y1) * (bbox_min_lon - x1) / (x2 - x1)
            x = bbox_min_lon
        elif code & 2:  # Phải
            y = y1 + (y2 - y1) * (bbox_max_lon - x1) / (x2 - x1)
            x = bbox_max_lon
        elif code & 4:  # Dưới
            x = x1 + (x2 - x1) * (bbox_min_lat - y1) / (y2 - y1)
            y = bbox_min_lat
        elif code & 8:  # Trên
            x = x1 + (x2 - x1) * (bbox_max_lat - y1) / (y2 - y1)
            y = bbox_max_lat

        # Kiểm tra xem giao điểm có nằm trong đoạn thẳng không
        if (min(x1, x2) <= x <= max(x1, x2)) and (min(y1, y2) <= y <= max(y1, y2)):
            clipped_points.append((x, y))
        else:
            return None

    return clipped_points[0], clipped_points[1] if len(clipped_points) == 2 else None

# Class để xử lý file OSM
class RoadGraphHandler(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.nodes = {}  # Lưu tọa độ node: {node_id: (lat, lon)}
        self.edges = []  # Lưu cạnh: [(node1, node2, weight, tags)]
        self.vehicle_highways = [
            'motorway', 'trunk', 'primary', 'secondary', 'tertiary',
            'unclassified', 'residential', 'service', 'track',
            'motorway_link', 'trunk_link', 'primary_link', 'secondary_link', 'tertiary_link'
        ]
        # Hộp giới hạn
        self.bbox_min_lat = 20.999906919559084
        self.bbox_min_lon = 105.82855224609376
        self.bbox_max_lat = 21.011445179194784
        self.bbox_max_lon = 105.8395278453827
        self.next_node_id = max([0] + [n for n in self.nodes.keys()]) + 1 if self.nodes else 1

    def node(self, n):
        lat, lon = n.location.lat, n.location.lon
        self.nodes[n.id] = (lat, lon)

    def way(self, w):
        if 'highway' not in w.tags:
            return

        highway_type = w.tags['highway']
        if highway_type not in self.vehicle_highways:
            logging.info(f"Bỏ qua way {w.id} vì không phải đường xe: highway={highway_type}")
            return

        is_oneway = w.tags.get('oneway', 'no') == 'yes'
        node_refs = [n.ref for n in w.nodes]
        tags = {t.k: t.v for t in w.tags}
        tags['id'] = str(w.id)

        # Xử lý từng đoạn của way
        valid_nodes = []
        for i in range(len(node_refs)):
            if node_refs[i] in self.nodes:
                lat, lon = self.nodes[node_refs[i]]
                valid_nodes.append((node_refs[i], lat, lon))

        # Tạo cạnh với cắt bỏ ngoài bbox
        for i in range(len(valid_nodes) - 1):
            n1, lat1, lon1 = valid_nodes[i]
            n2, lat2, lon2 = valid_nodes[i + 1]

            # Cắt đoạn thẳng nếu cần
            clipped = clip_segment_to_bbox(lon1, lat1, lon2, lat2,
                                          self.bbox_min_lat, self.bbox_min_lon,
                                          self.bbox_max_lat, self.bbox_max_lon)
            if not clipped:
                continue

            (clip_lon1, clip_lat1), (clip_lon2, clip_lat2) = clipped

            # Tạo node mới nếu điểm cắt không phải node gốc
            node1_id = n1
            if (clip_lon1, clip_lat1) != (lon1, lat1):
                node1_id = self.next_node_id
                self.nodes[node1_id] = (clip_lat1, clip_lon1)
                self.next_node_id += 1
                logging.info(f"Tạo node mới {node1_id} tại ({clip_lat1}, {clip_lon1})")

            node2_id = n2
            if (clip_lon2, clip_lat2) != (lon2, lat2):
                node2_id = self.next_node_id
                self.nodes[node2_id] = (clip_lat2, clip_lon2)
                self.next_node_id += 1
                logging.info(f"Tạo node mới {node2_id} tại ({clip_lat2}, {clip_lon2})")

            # Tính trọng số và thêm cạnh
            length = haversine(clip_lon1, clip_lat1, clip_lon2, clip_lat2)
            speed = get_speed(tags)
            weight = length / speed  # Trọng số là thời gian di chuyển (giờ)
            self.edges.append((node1_id, node2_id, weight, tags))
            if not is_oneway:
                self.edges.append((node2_id, node1_id, weight, tags))
            logging.info(f"Cạnh ({node1_id}, {node2_id}), way_id={tags['id']}, length={length:.3f} km, "
                         f"speed={speed} km/h, weight={weight:.6f} giờ")

def build_graph(osm_file, graphml_file='road_network.graphml'):
    logging.info(f"Xây dựng đồ thị từ file OSM: {osm_file}")
    handler = RoadGraphHandler()
    handler.apply_file(osm_file)

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