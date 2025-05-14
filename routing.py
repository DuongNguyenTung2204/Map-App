import networkx as nx
import math
import logging
import heapq
import json
import mysql.connector
from mysql.connector import Error

# Thiết lập logging cơ bản để ghi lại các sự kiện trong quá trình chạy chương trình
# - level=logging.INFO: Chỉ ghi lại các thông tin ở mức INFO trở lên (INFO, WARNING, ERROR, v.v.)
# - format: Định dạng log bao gồm thời gian, mức độ, và thông điệp
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Hàm kiểm tra kết nối đến cơ sở dữ liệu MySQL
# - Nhận đầu vào: db_config (dictionary chứa thông tin cấu hình như host, user, password, database)
# - Trả về: True nếu kết nối thành công, False nếu thất bại
def test_db_connection(db_config):
    try:
        # Kết nối đến cơ sở dữ liệu với cấu hình được cung cấp
        conn = mysql.connector.connect(**db_config)
        if conn.is_connected():
            # Nếu kết nối thành công, ghi log và đóng kết nối
            logging.info("Kết nối cơ sở dữ liệu thành công!")
            conn.close()
            return True
        else:
            # Nếu không thể kết nối, ghi log lỗi và trả về False
            logging.error("Kết nối cơ sở dữ liệu thất bại.")
            return False
    except Error as e:
        # Nếu có lỗi trong quá trình kết nối (ví dụ: sai mật khẩu, database không tồn tại), ghi log lỗi và trả về False
        logging.error(f"Lỗi kết nối cơ sở dữ liệu: {e}")
        return False

# Hàm tính khoảng cách Haversine giữa hai điểm dựa trên kinh độ (lon) và vĩ độ (lat)
# - Đầu vào: lon1, lat1 (tọa độ điểm 1), lon2, lat2 (tọa độ điểm 2)
# - Trả về: Khoảng cách (km) giữa hai điểm theo công thức Haversine
# - Công thức Haversine được dùng để tính khoảng cách trên bề mặt cầu (Trái Đất), phù hợp với tọa độ địa lý
def haversine(lon1, lat1, lon2, lat2):
    R = 6371  # Bán kính Trái Đất (km)
    # Chuyển đổi độ sang radian
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    # Công thức Haversine: tính khoảng cách dựa trên vĩ độ và kinh độ
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.asin(math.sqrt(a))
    return R * c

# Hàm tải đồ thị từ file GraphML
# - Đầu vào: graphml_file (đường dẫn đến file GraphML)
# - Trả về: Đồ thị G (networkx.DiGraph)
# - Mục đích: Đọc đồ thị đã được tạo trước (từ build_graph.py) để sử dụng trong việc tìm đường
def load_graph(graphml_file):
    logging.info(f"Tải đồ thị từ file GraphML: {graphml_file}")
    try:
        # Đọc file GraphML và tạo đồ thị có hướng (DiGraph)
        # node_type=int: Đảm bảo các node ID được đọc dưới dạng số nguyên
        G = nx.read_graphml(graphml_file, node_type=int)
        # Chuyển đổi thuộc tính lat, lon của mỗi node từ chuỗi sang số thực
        for node, data in G.nodes(data=True):
            data['lat'] = float(data['lat'])
            data['lon'] = float(data['lon'])
        # Ghi log thông tin về số lượng node và cạnh trong đồ thị
        logging.info(f"Đã tải đồ thị với {G.number_of_nodes()} node và {G.number_of_edges()} cạnh")
        return G
    except Exception as e:
        # Nếu có lỗi (ví dụ: file không tồn tại, định dạng sai), ghi log và ném ngoại lệ
        logging.error(f"Lỗi tải đồ thị: {str(e)}")
        raise

# Hàm lấy danh sách way bị tắc từ cơ sở dữ liệu
# - Đầu vào: db_config (cấu hình cơ sở dữ liệu)
# - Trả về: Tập hợp blocked_ways (các way_id dạng chuỗi)
# - Mục đích: Truy vấn bảng traffic_changes để lấy các way_id có trạng thái 'updated' (đường bị tắc)
def get_blocked_ways(db_config):
    try:
        # Kết nối đến cơ sở dữ liệu
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        # Truy vấn lấy way_id từ bảng traffic_changes với status = 'updated'
        query = "SELECT way_id FROM traffic_changes WHERE status = 'updated'"
        cursor.execute(query)
        # Chuyển đổi way_id thành chuỗi và lưu vào tập hợp
        blocked_ways = {str(row['way_id']) for row in cursor.fetchall()}
        cursor.close()
        conn.close()
        # Ghi log số lượng way bị tắc tìm thấy
        logging.info(f"Tìm thấy {len(blocked_ways)} way bị tắc")
        return blocked_ways
    except Exception as e:
        # Nếu có lỗi (ví dụ: truy vấn thất bại, không kết nối được), ghi log và trả về tập rỗng
        logging.error(f"Lỗi truy vấn traffic_changes: {str(e)}")
        return set()

# Hàm áp dụng phạt cho các cạnh thuộc way bị tắc
# - Đầu vào: G (đồ thị), blocked_ways (danh sách way_id bị tắc), penalty_factor (hệ số phạt, mặc định là 10)
# - Trả về: Đồ thị G_modified với trọng số đã được cập nhật
# - Mục đích: Tăng trọng số của các cạnh thuộc way bị tắc để thuật toán A* tránh các đường này
def apply_traffic_penalties(G, blocked_ways, penalty_factor=10):
    G_modified = G.copy()  # Tạo bản sao của đồ thị để không thay đổi đồ thị gốc
    blocked_edges = 0  # Đếm số cạnh bị phạt
    # Duyệt qua tất cả các cạnh trong đồ thị
    for u, v, data in G_modified.edges(data=True):
        # Phân tích tags (chuỗi JSON) của cạnh để lấy way_id
        tags = json.loads(data['tags'])
        way_id = tags.get('id')
        # Nếu way_id của cạnh nằm trong danh sách blocked_ways
        if way_id in blocked_ways:
            original_weight = data['weight']  # Lưu trọng số ban đầu
            data['weight'] = data['weight'] * penalty_factor  # Tăng trọng số lên penalty_factor lần
            blocked_edges += 1  # Tăng số đếm cạnh bị phạt
            # Ghi log chi tiết về cạnh bị phạt
            logging.info(f"Áp dụng phạt cho cạnh ({u}, {v}), way_id: {way_id}, trọng số cũ: {original_weight:.3f}, trọng số mới: {data['weight']:.3f}")
    # Ghi log tổng số cạnh bị phạt
    logging.info(f"Áp dụng phạt cho {blocked_edges} cạnh bị tắc (hệ số: {penalty_factor})")
    return G_modified

# Hàm chiếu một điểm (target_lat, target_lng) lên cạnh (u, v) trong đồ thị
# - Đầu vào: G (đồ thị), u, v (node của cạnh), target_lat, target_lng (tọa độ điểm cần chiếu)
# - Trả về: (proj_lat, proj_lon, dist, t)
#   - proj_lat, proj_lon: Tọa độ của điểm chiếu trên cạnh
#   - dist: Khoảng cách từ điểm cần chiếu đến điểm chiếu (km)
#   - t: Tỷ lệ vị trí của điểm chiếu trên cạnh (0 <= t <= 1)
# - Mục đích: Tìm điểm gần nhất trên cạnh (u, v) so với tọa độ đầu vào
def project_to_edge(G, u, v, target_lat, target_lng):
    u_lat, u_lon = G.nodes[u]['lat'], G.nodes[u]['lon']  # Tọa độ node u
    v_lat, v_lon = G.nodes[v]['lat'], G.nodes[v]['lon']  # Tọa độ node v
    # Tính vector từ u đến v
    dx = v_lon - u_lon
    dy = v_lat - u_lat
    length_sq = dx**2 + dy**2  # Độ dài bình phương của cạnh
    # Trường hợp đặc biệt: nếu u và v trùng nhau (cạnh có độ dài 0)
    if length_sq == 0:
        dist = haversine(u_lon, u_lat, target_lng, target_lat)
        return u_lat, u_lon, dist, 0
    # Tính tỷ lệ t (vị trí chiếu của điểm trên cạnh u-v)
    t = ((target_lng - u_lon) * dx + (target_lat - u_lat) * dy) / length_sq
    t = max(0, min(1, t))  # Đảm bảo t nằm trong khoảng [0, 1]
    # Tính tọa độ điểm chiếu
    proj_lon = u_lon + t * dx
    proj_lat = u_lat + t * dy
    # Tính khoảng cách từ điểm cần chiếu đến điểm chiếu
    dist = haversine(target_lng, target_lat, proj_lon, proj_lat)
    return proj_lat, proj_lon, dist, t

# Hàm snap một điểm (target_lat, target_lng) vào cạnh gần nhất trong đồ thị
# - Đầu vào: G (đồ thị), target_lat, target_lng (tọa độ điểm), max_distance_km (khoảng cách tối đa cho phép, mặc định 0.5 km)
# - Trả về: (node_id, G_modified)
#   - node_id: ID của node được snap (có thể là node hiện có hoặc node ảo mới)
#   - G_modified: Đồ thị đã được cập nhật (nếu thêm node ảo)
# - Mục đích: Tìm cạnh gần nhất với điểm và thêm một node ảo trên cạnh đó nếu cần
def snap_to_edge(G, target_lat, target_lng, max_distance_km=0.5):
    min_dist = float('inf')  # Khoảng cách nhỏ nhất từ điểm đến cạnh
    best_edge = None  # Cạnh gần nhất
    best_proj_lat = None  # Vĩ độ của điểm chiếu
    best_proj_lon = None  # Kinh độ của điểm chiếu
    best_t = None  # Tỷ lệ vị trí trên cạnh gần nhất
    # Định nghĩa vùng phủ sóng (giới hạn khu vực xử lý)
    bounds = {
        'min_lat': 21.00017,
        'min_lon': 105.82875,
        'max_lat': 21.01114,
        'max_lon': 105.83827
    }
    # Kiểm tra nếu điểm nằm ngoài vùng phủ sóng
    if not (bounds['min_lat'] <= target_lat <= bounds['max_lat'] and
            bounds['min_lon'] <= target_lng <= bounds['max_lon']):
        logging.warning(f"Tọa độ ({target_lat}, {target_lng}) ngoài vùng phủ sóng")
        return None, G
    # Duyệt qua tất cả các cạnh trong đồ thị để tìm cạnh gần nhất
    for u, v, data in G.edges(data=True):
        proj_lat, proj_lon, dist, t = project_to_edge(G, u, v, target_lat, target_lng)
        if dist < min_dist:
            min_dist = dist
            best_edge = (u, v)
            best_proj_lat = proj_lat
            best_proj_lon = proj_lon
            best_t = t
    # Nếu khoảng cách nhỏ nhất vượt quá ngưỡng cho phép, trả về None
    if min_dist > max_distance_km:
        logging.warning(f"Cạnh gần nhất cách {min_dist:.3f} km, vượt ngưỡng {max_distance_km} km")
        return None, G
    u, v = best_edge
    u_lat, u_lon = G.nodes[u]['lat'], G.nodes[u]['lon']
    v_lat, v_lon = G.nodes[v]['lat'], G.nodes[v]['lon']
    # Nếu điểm chiếu rất gần node u (t < 0.01), snap vào node u
    if best_t < 0.01 and haversine(u_lon, u_lat, target_lng, target_lat) <= max_distance_km:
        logging.info(f"Snap vào node hiện có {u}, khoảng cách {min_dist:.3f} km")
        return u, G
    # Nếu điểm chiếu rất gần node v (t > 0.99), snap vào node v
    if best_t > 0.99 and haversine(v_lon, v_lat, target_lng, target_lat) <= max_distance_km:
        logging.info(f"Snap vào node hiện có {v}, khoảng cách {min_dist:.3f} km")
        return v, G
    # Nếu không gần node u hoặc v, tạo node ảo mới trên cạnh (u, v)
    G_modified = G.copy()
    new_node = max(G.nodes) + 1  # Tạo ID mới cho node ảo
    G_modified.add_node(new_node, lat=best_proj_lat, lon=best_proj_lon)
    orig_weight = G[u][v]['weight']  # Lưu trọng số ban đầu của cạnh
    orig_tags = G[u][v]['tags']  # Lưu tags ban đầu của cạnh
    # Tính khoảng cách từ u đến node ảo và từ node ảo đến v
    dist_u_new = haversine(u_lon, u_lat, best_proj_lon, best_proj_lat)
    dist_new_v = haversine(best_proj_lon, best_proj_lat, v_lon, v_lat)
    # Xóa cạnh (u, v) cũ và thêm hai cạnh mới: (u, new_node) và (new_node, v)
    G_modified.remove_edge(u, v)
    G_modified.add_edge(u, new_node, weight=dist_u_new, tags=orig_tags)
    G_modified.add_edge(new_node, v, weight=dist_new_v, tags=orig_tags)
    logging.info(f"Snap vào node ảo {new_node} trên cạnh ({u}, {v}), khoảng cách {min_dist:.3f} km")
    return new_node, G_modified

# Hàm tìm đường ngắn nhất bằng thuật toán A*
# - Đầu vào: G (đồ thị), source (node bắt đầu), target (node kết thúc), end_lat, end_lng (tọa độ điểm kết thúc)
# - Trả về: Danh sách các node trên đường đi từ source đến target
# - Mục đích: Sử dụng A* để tìm đường ngắn nhất dựa trên trọng số của cạnh và heuristic
def a_star_path(G, source, target, end_lat, end_lng):
    # Hàm heuristic: Ước lượng khoảng cách từ node đến điểm kết thúc
    def heuristic(node):
        node_lat, node_lon = G.nodes[node]['lat'], G.nodes[node]['lon']
        return haversine(node_lon, node_lat, end_lng, end_lat)
    # Khởi tạo hàng đợi ưu tiên với chi phí ban đầu (f_score, node)
    open_set = [(0, source)]
    came_from = {}  # Lưu node cha để truy ngược đường đi
    g_score = {source: 0}  # Chi phí từ source đến node
    f_score = {source: heuristic(source)}  # Tổng chi phí (g_score + heuristic)
    while open_set:
        current_f, current = heapq.heappop(open_set)  # Lấy node có f_score nhỏ nhất
        if current == target:
            # Nếu đến được đích, truy ngược để tạo đường đi
            path = []
            while current in came_from:
                path.append(current)
                current = came_from[current]
            path.append(source)
            return path[::-1]  # Đảo ngược để có đường đi từ source đến target
        # Duyệt qua các node láng giềng của node hiện tại
        for neighbor in G.neighbors(current):
            tentative_g_score = g_score[current] + G[current][neighbor]['weight']
            if tentative_g_score < g_score.get(neighbor, float('inf')):
                # Nếu tìm được đường tốt hơn, cập nhật thông tin
                came_from[neighbor] = current
                g_score[neighbor] = tentative_g_score
                f_score[neighbor] = tentative_g_score + heuristic(neighbor)
                heapq.heappush(open_set, (f_score[neighbor], neighbor))
    return []  # Nếu không tìm thấy đường đi, trả về danh sách rỗng

# Hàm chính để tìm đường từ điểm bắt đầu đến điểm kết thúc
# - Đầu vào:
#   - start_lat, start_lng: Tọa độ điểm bắt đầu
#   - end_lat, end_lng: Tọa độ điểm kết thúc
#   - graphml_file: File GraphML chứa đồ thị
#   - db_config: Cấu hình cơ sở dữ liệu (mặc định None)
#   - penalty_factor: Hệ số phạt cho đường tắc (mặc định 100)
# - Trả về: Danh sách tọa độ trên đường đi (hoặc danh sách rỗng nếu không tìm thấy)
def find_route(start_lat, start_lng, end_lat, end_lng, 
               graphml_file='road_network.graphml', 
               db_config=None, 
               penalty_factor=100):
    """
    Tìm đường với A*, snap vào cạnh gần nhất, áp dụng phạt cho way bị tắc.
    db_config: Cấu hình MySQL (mặc định: localhost, map_app).
    penalty_factor: Hệ số phạt cho cạnh bị tắc.
    """
    # Khởi tạo db_config mặc định nếu không được cung cấp
    if db_config is None:
        db_config = {
            'host': 'localhost',
            'user': 'root',
            'password': '',
            'database': 'map_app'
        }
    
    try:
        # Kiểm tra kết nối cơ sở dữ liệu
        if not test_db_connection(db_config):
            logging.error("Không thể kết nối cơ sở dữ liệu. Bỏ qua thông tin tắc đường.")
            blocked_ways = set()
        else:
            # Nếu kết nối thành công, lấy danh sách way bị tắc
            blocked_ways = get_blocked_ways(db_config)
        
        # Tải đồ thị từ file GraphML
        G = load_graph(graphml_file)
        
        # Snap điểm bắt đầu vào cạnh gần nhất
        logging.info(f"Snap điểm bắt đầu ({start_lat}, {start_lng})")
        start_node, G = snap_to_edge(G, start_lat, start_lng)
        if start_node is None:
            logging.error("Không snap được điểm bắt đầu")
            return []
        
        # Snap điểm kết thúc vào cạnh gần nhất
        logging.info(f"Snap điểm kết thúc ({end_lat}, {end_lng})")
        end_node, G = snap_to_edge(G, end_lat, end_lng)
        if end_node is None:
            logging.error("Không snap được điểm kết thúc")
            return []
        
        # Áp dụng phạt cho các đường bị tắc nếu có
        if blocked_ways:
            G = apply_traffic_penalties(G, blocked_ways, penalty_factor)
        
        # Ghi log node bắt đầu và kết thúc
        logging.info(f"Node bắt đầu: {start_node}, Node kết thúc: {end_node}")
        
        # Kiểm tra tính liên thông giữa hai node
        undirected_G = G.to_undirected()
        if not nx.has_path(undirected_G, start_node, end_node):
            logging.error(f"Không có đường nối giữa node {start_node} và {end_node}")
            return []
        
        # Tìm đường bằng thuật toán A*
        path = a_star_path(G, start_node, end_node, end_lat, end_lng)
        if not path:
            logging.error(f"Không tìm thấy đường đi từ ({start_lat}, {start_lng}) đến ({end_lat}, {end_lng})")
            return []
        
        # Ghi log đường đi
        logging.info(f"Đường đi: {path}")
        # Chuyển đổi đường đi thành danh sách tọa độ
        route_coords = [[G.nodes[node]['lat'], G.nodes[node]['lon']] for node in path]
        return route_coords
    
    except Exception as e:
        # Nếu có lỗi tổng quát, ghi log và trả về danh sách rỗng
        logging.error(f"Lỗi: {str(e)}")
        return []